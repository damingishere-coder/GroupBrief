"""邮件服务。

每天只发送一封邮件，包含所有启用群，每个群 = 排行榜 + GPT 生图 Prompt。
- 中文 / emoji 使用 UTF-8
- 发送前检查每个群的文件与状态，不发送空白结果
- SEND_PARTIAL_REPORT=true 时，部分群失败仍发送成功群
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.models import GroupRun, Report, Run
from app.scheduler.calendar_rules import email_subject, get_report_window

logger = get_logger("groupbrief.email")


@dataclass
class GroupMailBlock:
    group_name: str
    ranking_text: str
    prompt_text: str


@dataclass
class EmailBuildResult:
    subject: str
    body: str
    blocks: list[GroupMailBlock] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class EmailService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def build_email(self, session: Session, run: Run | None = None) -> EmailBuildResult:
        """组装当天邮件内容（只含排行榜 + Prompt）。"""
        window = get_report_window(timezone=self.settings.app_timezone)
        subject = email_subject(window)

        # 优先使用指定 run 的 group_runs；否则取最近成功 run
        if run is None:
            runs = repo.find_runs(session, 10)
            run = next((r for r in runs if r.status in ("success", "partial")), None)

        blocks: list[GroupMailBlock] = []
        missing: list[str] = []
        if run:
            from sqlmodel import select

            group_runs = session.exec(select(GroupRun).where(GroupRun.run_id == run.id)).all()
            for gr in group_runs:
                if gr.ranking_status != "success":
                    missing.append(f"群 {gr.group_id}：排行榜未生成（{gr.ranking_status}）")
                    continue
                report = repo.get_report_by_group_run(session, gr.id)
                if report is None or not report.ranking_text:
                    missing.append(f"群 {gr.group_id}：报告数据缺失")
                    continue
                group = repo.get_group(session, gr.group_id)
                group_name = (group.display_name or group.wechat_group_name) if group else f"群 {gr.group_id}"
                blocks.append(
                    GroupMailBlock(
                        group_name=group_name,
                        ranking_text=report.ranking_text,
                        prompt_text=report.prompt_text or "",
                    )
                )
            if not blocks:
                missing.append("没有可发送的群报告")

        body_lines: list[str] = []
        for b in blocks:
            body_lines.append(f"===== {b.group_name} =====")
            body_lines.append("")
            body_lines.append("【发言排行榜】")
            body_lines.append("")
            body_lines.append(b.ranking_text)
            body_lines.append("")
            body_lines.append("【GPT 生图 Prompt】")
            body_lines.append("")
            body_lines.append(b.prompt_text if b.prompt_text else "（未生成）")
            body_lines.append("")
            body_lines.append("")

        return EmailBuildResult(
            subject=subject,
            body="\n".join(body_lines),
            blocks=blocks,
            missing=missing,
        )

    def send(self, session: Session, run: Run | None = None) -> tuple[bool, str]:
        if not self.settings.email_enabled or not self.settings.email_smtp_host:
            return False, "邮件未启用或未配置 SMTP"

        result = self.build_email(session, run)
        if not result.blocks:
            return False, "没有可发送的群报告（全部失败或无数据）"
        if result.missing and not self.settings.email_send_partial_report:
            return False, f"存在失败群且 SEND_PARTIAL_REPORT=false：{result.missing[0]}"

        subject = result.subject
        body = result.body
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.email_from or self.settings.email_smtp_user
        message["To"] = self.settings.email_recipient
        message.set_content(body)

        try:
            if self.settings.email_use_ssl:
                server = smtplib.SMTP_SSL(
                    self.settings.email_smtp_host,
                    self.settings.email_smtp_port,
                    timeout=30,
                )
            else:
                server = smtplib.SMTP(
                    self.settings.email_smtp_host,
                    self.settings.email_smtp_port,
                    timeout=30,
                )
                server.starttls()
            try:
                if self.settings.email_smtp_user:
                    server.login(
                        self.settings.email_smtp_user,
                        self.settings.email_smtp_password,
                    )
                server.send_message(message)
            finally:
                server.quit()
        except Exception as e:
            logger.exception("邮件发送失败")
            return False, f"SMTP 错误：{str(e)[:300]}"

        logger.info("邮件已发送：%s 收件人=%s 群数=%d", subject, self.settings.email_recipient, len(result.blocks))
        return True, "sent"
