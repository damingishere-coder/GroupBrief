"""P6 测试：邮件内容组装（不真实发信）。"""

import json
from datetime import datetime

from sqlmodel import Session, select

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import Group, GroupRun
from app.services.email_service import EmailService
from app.services.report_service import ReportService

settings = get_settings()
settings.ensure_dirs()
repo.init_db(settings)


def _prepare_run(session: Session) -> int:
    group = repo.save_group(
        session,
        Group(display_name="Eason张UED-4群", wechat_group_id="group-a"),
    )
    service = ReportService()
    run = service.generate(session, group=group, report_date="2026-08-13", force=True)
    return run.id


def test_email_build_contains_ranking_and_prompt():
    with Session(repo.engine) as session:
        run_id = _prepare_run(session)
        run = repo.get_run(session, run_id)
        service = EmailService()
        result = service.build_email(session, run)

        assert result.blocks, "应至少有一个群块"
        assert "=====" in result.body
        assert "【发言排行榜】" in result.body
        assert "【GPT 生图 Prompt】" in result.body
        # 邮件不应包含额外分析内容
        assert "今日洞察" not in result.body
        assert "AI 总结" not in result.body
        # 排行榜完整
        assert "消息统计" in result.body
        assert "发言 Top10" in result.body


def test_email_subject_weekend():
    with Session(repo.engine) as session:
        service = EmailService()
        result = service.build_email(session)
        # 周一：周末汇总主题
        assert "周末汇总" in result.subject or "｜" in result.subject


def test_email_not_sent_without_config():
    settings2 = Settings(email_enabled=False)
    service = EmailService(settings2)
    with Session(repo.engine) as session:
        ok, detail = service.send(session)
        assert not ok
        assert "未启用" in detail


def test_email_send_partial_flag():
    settings2 = Settings(email_enabled=True, email_smtp_host="smtp.example.com", email_send_partial_report=False)
    service = EmailService(settings2)
    with Session(repo.engine) as session:
        # 没有已生成报告时不应尝试发送
        ok, detail = service.send(session)
        assert not ok
