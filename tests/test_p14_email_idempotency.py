"""P1.4 邮件幂等账本测试；不连接真实 SMTP。"""

from __future__ import annotations

import json
from email.message import EmailMessage
from types import SimpleNamespace

from app.config.settings import Settings
from app.scheduler.outcome import ProcessExitCode
from app.services.email_delivery import (
    EmailDeliveryLedger,
    deliver_email,
    ensure_email_identity,
)


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_smtp_port=465,
        email_recipient="to@example.com",
        email_from="from@example.com",
        email_use_ssl=True,
    )


def _message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "群报 GroupBrief｜测试群｜2026-08-24"
    message["From"] = "from@example.com"
    message["To"] = "to@example.com"
    message.set_content("排行榜正文")
    return message


def test_sent_message_is_skipped_with_same_stable_message_id(tmp_path, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connect")

        def send_message(self, message):
            calls.append(str(message["Message-ID"]))
            return {}

        def quit(self):
            return None

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP_SSL", FakeSMTP)
    ledger = EmailDeliveryLedger(tmp_path / "ledger")
    first = deliver_email(_message(), _settings(tmp_path), ledger=ledger)
    second_message = _message()
    second = deliver_email(second_message, _settings(tmp_path), ledger=ledger)

    assert first.status == "sent"
    assert second.status == "already_sent"
    assert first.message_id == second.message_id == str(second_message["Message-ID"])
    assert calls.count("connect") == 1


def test_send_message_exception_becomes_unknown_and_never_retries(tmp_path, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls.append("connect")

        def send_message(self, message):
            calls.append("submit")
            raise RuntimeError("connection lost after submit")

        def quit(self):
            return None

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP_SSL", FakeSMTP)
    ledger = EmailDeliveryLedger(tmp_path / "ledger")
    first = deliver_email(_message(), _settings(tmp_path), ledger=ledger, max_attempts=3)
    second = deliver_email(_message(), _settings(tmp_path), ledger=ledger, max_attempts=3)

    assert first.status == "unknown"
    assert second.status == "unknown"
    assert calls == ["connect", "submit"]


def test_crash_after_submitting_marker_is_held_without_smtp(tmp_path, monkeypatch):
    ledger = EmailDeliveryLedger(tmp_path / "ledger")
    message = _message()
    identity = ensure_email_identity(message)
    with ledger.transaction(identity) as transaction:
        transaction.mark_submitting()

    def fail_connect(*args, **kwargs):
        raise AssertionError("submitting 状态恢复时不得连接 SMTP")

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP_SSL", fail_connect)
    result = deliver_email(_message(), _settings(tmp_path), ledger=ledger)
    assert result.status == "unknown"


def test_connection_failure_retries_only_before_submit(tmp_path, monkeypatch):
    calls = []

    def fail_connect(*args, **kwargs):
        calls.append("connect")
        raise OSError("cannot connect")

    monkeypatch.setattr("app.services.email_delivery.smtplib.SMTP_SSL", fail_connect)
    monkeypatch.setattr("app.services.email_delivery.time.sleep", lambda _seconds: None)
    ledger = EmailDeliveryLedger(tmp_path / "ledger")
    message = _message()
    identity = ensure_email_identity(message)
    result = deliver_email(message, _settings(tmp_path), ledger=ledger, max_attempts=2)

    assert result.status == "failed_before_submit"
    assert calls == ["connect", "connect"]
    record = json.loads(ledger.path_for(identity).read_text(encoding="utf-8"))
    assert record["state"] == "failed_before_submit"


def test_scheduler_maps_email_blocked_exit_to_unknown_hold(tmp_path, monkeypatch):
    from app.scheduler import daily_v2_job as daily

    settings = _settings(tmp_path)
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class SuccessPipeline:
        def __init__(self, settings):
            pass

        def generate_all(self, run_date, acquire_lock=True):
            return [{"group_name": "测试群", "status": "ready_to_send"}]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", SuccessPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=int(ProcessExitCode.BLOCKED),
            stdout="one group unknown",
            stderr="",
        ),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "blocked"
    assert result["email_status"] == "unknown"
    assert result["error_type"] == "EMAIL_RESULT_UNKNOWN"
    state = TempState(tmp_path).load("2026-08-25")
    assert state["email_hold"] is True
