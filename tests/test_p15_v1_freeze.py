"""P1.5 旧 V1 双轨冻结测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import email as email_api
from app.api import reports as reports_api
from app.config.settings import Settings, get_settings
from app.main import app
from app.scheduler.email_job import run_email_job
from app.scheduler.generate_job import run_generate_job
from app.services.email_service import EmailService
from app.services.legacy_v1_policy import (
    LEGACY_V1_WRITE_BLOCKED,
    LegacyV1WriteBlockedError,
)
from app.services.report_service import ReportService


def _read_only_settings(tmp_path=None) -> Settings:
    values = {
        "_env_file": None,
        "legacy_v1_write_mode": "read_only",
        "email_enabled": True,
        "email_smtp_host": "smtp.example.com",
        "email_recipient": "to@example.com",
        "email_from": "from@example.com",
    }
    if tmp_path is not None:
        values["database_url"] = f"sqlite:///{(tmp_path / 'blocked.db').as_posix()}"
    return Settings(**values)


def test_legacy_v1_write_mode_defaults_to_read_only(monkeypatch):
    monkeypatch.delenv("LEGACY_V1_WRITE_MODE", raising=False)
    assert Settings(_env_file=None).legacy_v1_write_mode == "read_only"


def test_report_service_blocks_before_generation_mutex(monkeypatch):
    monkeypatch.setattr(
        "app.services.report_service.generation_mutex",
        lambda: pytest.fail("只读模式不得进入生成锁"),
    )
    service = ReportService(settings=_read_only_settings())

    with pytest.raises(LegacyV1WriteBlockedError):
        service.generate(None)


def test_email_service_blocks_before_build_or_smtp(monkeypatch):
    service = EmailService(_read_only_settings())
    monkeypatch.setattr(
        service,
        "build_email",
        lambda *args, **kwargs: pytest.fail("只读模式不得读取并发送旧报告"),
    )

    with pytest.raises(LegacyV1WriteBlockedError):
        service.send(None)


def test_legacy_write_apis_return_410_without_calling_services(monkeypatch):
    settings = _read_only_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(
        reports_api.ReportService,
        "generate",
        lambda *args, **kwargs: pytest.fail("冻结 API 不得调用 ReportService"),
    )
    monkeypatch.setattr(
        email_api.EmailService,
        "send",
        lambda *args, **kwargs: pytest.fail("冻结 API 不得调用 EmailService"),
    )
    try:
        with TestClient(app) as client:
            generated = client.post("/api/reports/generate", json={})
            updated = client.put("/api/reports/999/prompt", json={"text": "不应写入"})
            sent = client.post("/api/email/send")
    finally:
        app.dependency_overrides.clear()

    for response in (generated, updated, sent):
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == LEGACY_V1_WRITE_BLOCKED


def test_legacy_read_apis_remain_available():
    with TestClient(app) as client:
        assert client.get("/api/reports/latest").status_code == 200
        assert client.get("/api/runs").status_code == 200
        assert client.get("/api/email/preview").status_code == 200


def test_legacy_scheduler_jobs_return_blocked_without_services(monkeypatch):
    monkeypatch.setattr(
        "app.scheduler.generate_job.Session",
        lambda *args, **kwargs: pytest.fail("冻结 job 不得打开数据库 Session"),
    )
    monkeypatch.setattr(
        "app.scheduler.email_job.Session",
        lambda *args, **kwargs: pytest.fail("冻结 job 不得打开数据库 Session"),
    )
    settings = _read_only_settings()

    generate_result = run_generate_job(settings)
    email_result = run_email_job(settings)

    for result in (generate_result, email_result):
        assert result["status"] == "blocked"
        assert result["error_type"] == LEGACY_V1_WRITE_BLOCKED
        assert result["exit_code"] == 3


def test_maintenance_mode_is_explicit_and_not_settings_api_editable():
    from app.api.settings import EDITABLE_KEYS

    settings = Settings(_env_file=None, legacy_v1_write_mode="maintenance")
    assert settings.legacy_v1_write_mode == "maintenance"
    assert "legacy_v1_write_mode" not in EDITABLE_KEYS

    read_only = _read_only_settings()
    assert read_only.apply_runtime_values({"legacy_v1_write_mode": "maintenance"}) == []
    assert read_only.legacy_v1_write_mode == "read_only"


def test_system_status_exposes_freeze_mode():
    settings = _read_only_settings()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get("/api/system/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["legacy_v1_write_mode"] == "read_only"
    assert response.json()["legacy_v1_writes_active"] is False
