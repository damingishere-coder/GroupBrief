"""数据库报告邮件服务。

每个启用群独立发送一封邮件，正文只交付排行榜，图片仅使用 Report.poster_file。
- 中文 / emoji 使用 UTF-8
- 发送前检查每个群的报告与状态，不发送空白结果
- 单群 SMTP 失败不阻塞后续群，并汇总最终结果
"""

from __future__ import annotations

import smtplib  # 兼容旧测试注入；真实发送实现在 email_delivery
import time  # 兼容旧测试注入；真实退避实现在 email_delivery
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.models import GroupRun, Report, Run
from app.image.image_task import detect_image_format, verify_image
from app.scheduler.calendar_rules import email_subject, get_report_window
from app.services.email_delivery import EmailDeliveryLedger, deliver_email
from app.services.handoff_service import safe_dir_name
from app.services.legacy_v1_policy import require_legacy_v1_write

logger = get_logger("groupbrief.email")


def email_delivery_config_error(settings: Settings) -> str:
    """返回真实邮件发送前的配置错误；空字符串表示配置完整。"""
    if not settings.email_enabled:
        return "邮件未启用"
    if not str(settings.email_smtp_host or "").strip():
        return "邮件 SMTP 主机未配置"
    try:
        port = int(settings.email_smtp_port)
    except (TypeError, ValueError):
        return "邮件 SMTP 端口无效"
    if not 1 <= port <= 65535:
        return "邮件 SMTP 端口无效"
    if not str(settings.email_recipient or "").strip():
        return "邮件收件人未配置"
    if not str(settings.email_from or settings.email_smtp_user or "").strip():
        return "邮件发件人未配置"
    if settings.email_smtp_user and not settings.email_smtp_password:
        return "邮件 SMTP 用户已配置但密码缺失"
    return ""


@dataclass
class GroupMailBlock:
    group_name: str
    ranking_text: str
    prompt_text: str = ""  # 兼容旧调用；Prompt 不作为邮件正文交付
    period_start: str = ""
    period_end: str = ""
    poster_file: str = ""


@dataclass
class EmailBuildResult:
    subject: str
    body: str
    blocks: list[GroupMailBlock] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class EmailService:
    def __init__(
        self,
        settings: Settings | None = None,
        delivery_ledger: EmailDeliveryLedger | None = None,
    ):
        self.settings = settings or get_settings()
        self.delivery_ledger = delivery_ledger

    def build_email(self, session: Session, run: Run | None = None) -> EmailBuildResult:
        """读取各群邮件数据；body 只拼接排行榜原文，不再额外包装。"""
        window = get_report_window(timezone=self.settings.app_timezone)
        fallback_start = window.range_start.isoformat()
        fallback_end = window.range_end.isoformat()
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
                if gr.identity_state != "linked" or gr.group_id is None:
                    missing.append(
                        f"历史群（旧 ID {gr.legacy_group_id}）：关联已归档，不发送"
                    )
                    continue
                if gr.ranking_status != "success":
                    missing.append(f"群 {gr.group_id}：排行榜未生成（{gr.ranking_status}）")
                    continue
                group = repo.get_active_group(session, gr.group_id)
                if group is None or not group.enabled:
                    # 停用/已删除的群不发送（如用户停用了不需要的群）
                    continue
                report = repo.get_report_by_group_run(session, gr.id)
                if report is None or not (report.ranking_text or "").strip():
                    missing.append(f"群 {gr.group_id}：报告数据缺失")
                    continue
                group_name = (group.display_name or group.wechat_group_name) if group else f"群 {gr.group_id}"
                blocks.append(
                    GroupMailBlock(
                        group_name=group_name,
                        ranking_text=(report.ranking_text or "").strip(),
                        prompt_text=report.prompt_text or "",
                        period_start=run.range_start or fallback_start,
                        period_end=run.range_end or fallback_end,
                        poster_file=report.poster_file or "",
                    )
                )
            if not blocks:
                missing.append("没有可发送的群报告")

        if blocks:
            first = blocks[0]
            start_date = (first.period_start or "")[:10]
            end_date = (first.period_end or "")[:10] or start_date
            period = start_date if start_date == end_date else f"{start_date}～{end_date}"
            subject = f"群报 GroupBrief｜{first.group_name}｜{period}"
        body = "\n\n".join(block.ranking_text for block in blocks)

        return EmailBuildResult(
            subject=subject,
            body=body,
            blocks=blocks,
            missing=missing,
        )

    def send(self, session: Session, run: Run | None = None) -> tuple[bool, str]:
        require_legacy_v1_write(
            self.settings,
            operation="email.send",
            replacement="V2 每日任务或 scripts/send_daily_email.py",
        )
        config_error = email_delivery_config_error(self.settings)
        if config_error:
            return False, config_error

        result = self.build_email(session, run)
        if not result.blocks:
            return False, "没有可发送的群报告（全部失败或无数据）"
        if result.missing and not self.settings.email_send_partial_report:
            return False, f"存在失败群且 SEND_PARTIAL_REPORT=false：{result.missing[0]}"

        sent_count = 0
        # 允许发送部分报告不等于可以把缺失群伪装成全量成功。
        failed_count = len(result.missing)
        details: list[str] = []
        for block in result.blocks:
            try:
                message = self._build_group_message(block)
            except Exception as exc:
                failed_count += 1
                error = str(exc)
                details.append(f"{block.group_name}=失败({error[:200]})")
                logger.warning("群报构造失败 group=%s error=%s", block.group_name, error[:200])
                continue
            ok, error = self._send_group_message(message)
            if ok:
                sent_count += 1
                details.append(f"{block.group_name}=成功")
                continue
            failed_count += 1
            details.append(f"{block.group_name}=失败({error[:200]})")
            # 这里故意只记录当前群失败，不中断后续群。
            logger.warning("群报发送失败 group=%s error=%s", block.group_name, error[:200])

        if result.missing:
            details.append(f"跳过 {len(result.missing)} 个无可用报告群")
        summary = f"成功 {sent_count} 个群，失败 {failed_count} 个群；" + "；".join(details)
        if sent_count > 0 and failed_count == 0:
            logger.info(
                "群报邮件已发送：收件人=%s 成功=%d 跳过=%d",
                self.settings.email_recipient,
                sent_count,
                len(result.missing),
            )
            return True, summary
        return False, summary

    def _build_group_message(self, block: GroupMailBlock) -> EmailMessage:
        """构造单群邮件；poster_file 无效时兼容降级为纯排行榜。"""
        start_date = (block.period_start or "")[:10]
        end_date = (block.period_end or "")[:10] or start_date
        period = start_date if start_date == end_date else f"{start_date}～{end_date}"
        message = EmailMessage()
        message["Subject"] = f"群报 GroupBrief｜{block.group_name}｜{period}"
        message["From"] = self.settings.email_from or self.settings.email_smtp_user
        message["To"] = self.settings.email_recipient
        message.set_content(block.ranking_text.strip())

        if not block.poster_file:
            return message
        image_path = Path(block.poster_file)
        ok, detail = verify_image(image_path)
        if not ok:
            logger.warning("群 %s 的 poster_file 无效，跳过附件：%s", block.group_name, detail[:200])
            return message
        image_format = detect_image_format(image_path)
        mime_types = {
            "png": "image/png",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "tiff": "image/tiff",
            "bmp": "image/bmp",
        }
        mime_type = mime_types.get(image_format or "")
        if mime_type is None:
            logger.warning("群 %s 的 poster_file MIME 类型未知，跳过附件", block.group_name)
            return message
        try:
            image_data = image_path.read_bytes()
        except OSError as exc:
            logger.warning("群 %s 的 poster_file 无法读取，跳过附件：%s", block.group_name, str(exc)[:200])
            return message
        message.add_attachment(
            image_data,
            maintype="image",
            subtype=mime_type.removeprefix("image/"),
            filename=f"{safe_dir_name(block.group_name)}-日报图片.{image_format}",
        )
        return message

    def _send_group_message(self, message: EmailMessage, max_attempts: int = 2) -> tuple[bool, str]:
        """兼容 V1 调用形态；实际幂等和重试策略由统一交付模块负责。"""
        result = deliver_email(
            message,
            self.settings,
            ledger=self.delivery_ledger,
            max_attempts=max_attempts,
        )
        if result.success:
            return True, result.detail
        if result.outcome_unknown:
            return False, f"结果未知，已禁止自动重发：{result.detail}"
        return False, result.detail
