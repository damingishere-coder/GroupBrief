"""P6 测试：邮件内容组装（不真实发信）。"""

import json
from datetime import datetime

import app.services.email_service as email_module
from PIL import Image
from sqlmodel import Session, select

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import Group, GroupRun
from app.services.email_delivery import EmailDeliveryLedger
from app.services.email_service import EmailBuildResult, EmailService, GroupMailBlock
from app.services.history_service import HistoryService
from app.services.prompt_service import PromptService
from app.services.report_service import ReportService

settings = get_settings()
settings.ensure_dirs()
repo.init_db(settings)


def _prepare_run(session: Session) -> int:
    group = repo.save_group(
        session,
        Group(display_name="示例UED-4群", wechat_group_id="group-a"),
    )
    test_settings = Settings(
        _env_file=None,
        allow_test_providers=True,
        history_provider_primary="mock",
        history_provider_fallback="",
        history_provider_mock_enabled=True,
        summary_provider_primary="deepseek",
        ai_api_key="",
    )
    service = ReportService(
        history=HistoryService(test_settings),
        prompt=PromptService(test_settings),
    )
    run = service.generate(session, group=group, report_date="2026-08-13", force=True)
    return run.id


def test_email_build_uses_ranking_without_wrappers_or_prompt():
    with Session(repo.engine) as session:
        run_id = _prepare_run(session)
        run = repo.get_run(session, run_id)
        service = EmailService()
        result = service.build_email(session, run)

        assert result.blocks, "应至少有一个群块"
        ranking_text = result.blocks[0].ranking_text
        assert result.body == ranking_text
        assert result.body.count("===== ") == ranking_text.count("===== ")
        assert result.body.count("【发言排行榜】") == ranking_text.count("【发言排行榜】")
        assert "【GPT 生图 Prompt】" not in result.body
        # 邮件不应包含额外分析内容
        assert "今日洞察" not in result.body
        assert "AI 总结" not in result.body
        # 排行榜完整
        assert "消息统计" in result.body
        assert "发言 Top10" in result.body


def test_email_subject_has_no_weekend_summary():
    with Session(repo.engine) as session:
        service = EmailService()
        result = service.build_email(session)
        assert "周末汇总" not in result.subject
        assert "群报 GroupBrief｜" in result.subject


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


def test_email_partial_flag_aborts_before_smtp(monkeypatch):
    settings2 = Settings(
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
        email_send_partial_report=False,
    )
    service = EmailService(settings2)
    block = GroupMailBlock(group_name="可发送群", ranking_text="排行榜")
    monkeypatch.setattr(
        service,
        "build_email",
        lambda session, run=None: EmailBuildResult(
            subject="unused",
            body="unused",
            blocks=[block],
            missing=["缺失群：报告数据缺失"],
        ),
    )
    smtp_calls = []

    def fail_if_connected(*args, **kwargs):
        smtp_calls.append((args, kwargs))
        raise AssertionError("SEND_PARTIAL_REPORT=false 时不应连接 SMTP")

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", fail_if_connected)
    with Session(repo.engine) as session:
        ok, detail = service.send(session)

    assert not ok
    assert "SEND_PARTIAL_REPORT=false" in detail
    assert not smtp_calls


def test_email_partial_delivery_does_not_report_full_success(monkeypatch):
    settings2 = Settings(
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
        email_send_partial_report=True,
    )
    service = EmailService(settings2)
    block = GroupMailBlock(group_name="可发送群", ranking_text="排行榜")
    monkeypatch.setattr(
        service,
        "build_email",
        lambda session, run=None: EmailBuildResult(
            subject="unused",
            body="unused",
            blocks=[block],
            missing=["缺失群：报告数据缺失"],
        ),
    )
    monkeypatch.setattr(service, "_send_group_message", lambda message: (True, ""))

    with Session(repo.engine) as session:
        ok, detail = service.send(session)

    assert not ok
    assert "成功 1 个群" in detail
    assert "失败 1 个群" in detail


def test_email_invalid_config_aborts_before_smtp(monkeypatch):
    settings2 = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="",
        email_from="from@example.com",
    )
    service = EmailService(settings2)
    smtp_calls = []

    def fail_if_connected(*args, **kwargs):
        smtp_calls.append((args, kwargs))
        raise AssertionError("配置无效时不应连接 SMTP")

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", fail_if_connected)
    with Session(repo.engine) as session:
        ok, detail = service.send(session)

    assert not ok
    assert "收件人" in detail
    assert not smtp_calls


def test_email_quit_failure_does_not_retry(tmp_path, monkeypatch):
    settings2 = Settings(email_enabled=True, email_smtp_host="smtp.example.com")
    service = EmailService(
        settings2,
        delivery_ledger=EmailDeliveryLedger(tmp_path / "ledger"),
    )
    calls = {"connect": 0, "send": 0, "sleep": 0}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls["connect"] += 1

        def send_message(self, message):
            calls["send"] += 1

        def quit(self):
            raise RuntimeError("fake quit failure")

    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_module.time, "sleep", lambda seconds: calls.__setitem__("sleep", calls["sleep"] + 1))
    message = service._build_group_message(
        GroupMailBlock(
            group_name="测试群",
            ranking_text="排行榜",
            period_start="2026-08-20 00:00:00",
            period_end="2026-08-20 23:59:59",
        )
    )

    ok, detail = service._send_group_message(message)

    assert ok
    assert detail == ""
    assert calls == {"connect": 1, "send": 1, "sleep": 0}


def test_email_send_is_per_group_and_attaches_valid_poster(tmp_path, monkeypatch):
    poster = tmp_path / "poster.png"
    Image.new("RGBA", (2, 2), (20, 40, 60, 255)).save(poster, format="PNG")
    settings2 = Settings(
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
    )
    service = EmailService(
        settings2,
        delivery_ledger=EmailDeliveryLedger(tmp_path / "ledger"),
    )
    blocks = [
        GroupMailBlock(
            group_name="失败群",
            ranking_text="排行榜失败",
            period_start="2026-08-20 00:00:00",
            period_end="2026-08-20 23:59:59",
        ),
        GroupMailBlock(
            group_name="成功群",
            ranking_text="排行榜成功",
            period_start="2026-08-20 00:00:00",
            period_end="2026-08-20 23:59:59",
            poster_file=str(poster),
        ),
    ]

    class FakeSMTP:
        instances = []

        def __init__(self, *args, **kwargs):
            self.messages = []
            self.__class__.instances.append(self)

        def send_message(self, message):
            self.messages.append(message)
            if "失败群" in str(message["Subject"]):
                raise RuntimeError("fake failure")

        def quit(self):
            return None

    monkeypatch.setattr(service, "build_email", lambda session, run=None: EmailBuildResult(
        subject="unused", body="unused", blocks=blocks
    ))
    monkeypatch.setattr(email_module.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(email_module.time, "sleep", lambda seconds: None)

    with Session(repo.engine) as session:
        ok, detail = service.send(session)

    assert not ok
    assert "成功 1 个群" in detail
    all_messages = [message for instance in FakeSMTP.instances for message in instance.messages]
    assert sum("失败群" in str(message["Subject"]) for message in all_messages) == 1
    successful = [message for message in all_messages if "成功群" in str(message["Subject"])]
    assert len(successful) == 1
    assert successful[0].get_body(preferencelist=("plain",)).get_content().strip() == "排行榜成功"
    assert len(list(successful[0].iter_attachments())) == 1
