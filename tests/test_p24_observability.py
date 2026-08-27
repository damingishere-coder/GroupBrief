from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import system
from app.api import v2_ui
from app.config.settings import get_settings
from app.core.logging import setup_logging
from app.db import repository as repo
from app.db.models import ProviderHealth
from app.main import _capture_startup_checks
from app.image.codex_generator import CodexImageGenerator
from app.providers.ai.codex import CodexGPTProvider
from app.sender.wechat_native import WechatNativeSender


def test_setup_logging_configures_category_files_when_root_has_handler(tmp_path):
    root = logging.getLogger()
    root_handler = logging.NullHandler()
    root.addHandler(root_handler)
    target = str((tmp_path / "scheduler.log").resolve())
    category_names = (
        "app",
        "groupbrief.providers",
        "groupbrief.ai",
        "groupbrief.scheduler",
        "groupbrief.email",
    )
    before = {
        name: list(logging.getLogger(name).handlers) for name in category_names
    }
    scheduler_logger = logging.getLogger("groupbrief.scheduler")
    try:
        setup_logging(tmp_path)
        matching = [
            handler
            for handler in scheduler_logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and handler.baseFilename == target
        ]
        assert len(matching) == 1

        setup_logging(tmp_path)
        matching = [
            handler
            for handler in scheduler_logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and handler.baseFilename == target
        ]
        assert len(matching) == 1
    finally:
        root.removeHandler(root_handler)
        for name in category_names:
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                if handler not in before[name]:
                    logger.removeHandler(handler)
                    handler.close()


def _readiness_client(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def override_session():
        with Session(engine) as session:
            yield session

    api = FastAPI()
    api.include_router(system.router)
    api.state.startup_check_error = ""
    api.state.startup_checks = []
    api.state.startup_checks_at = "2026-08-27T00:00:00+08:00"
    api.state.scheduler_owner = "windows"
    api.state.scheduler_active = False
    api.dependency_overrides[repo.get_session] = override_session
    api.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        output_dir=output_dir,
        scheduler_owner="windows",
        app_timezone="Asia/Shanghai",
        schedule_generate_time="23:59",
        reliability_watchdog_interval_minutes=10,
        scheduler_heartbeat_stale_seconds=300,
    )
    return TestClient(api), api


def test_readiness_is_local_read_only_and_exposes_startup_capture_error(tmp_path):
    client, api = _readiness_client(tmp_path)
    with client:
        ready = client.get("/api/system/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True
        assert set(ready.json()["checks"]) == {
            "database",
            "output",
            "templates",
            "startup_capture",
            "wechat_data",
            "wechat_client",
            "summary_provider",
            "scheduler_heartbeat",
            "daily_completion",
        }

        api.state.startup_check_error = "startup probe crashed"
        failed = client.get("/api/system/ready")
        assert failed.status_code == 503
        assert failed.json()["ready"] is False
        assert failed.json()["checks"]["startup_capture"]["status"] == "ERROR"


def test_provider_health_retention_keeps_latest_one_hundred_per_provider():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(105):
            session.add(
                ProviderHealth(
                    provider="provider-a",
                    status="OK",
                    detail=str(index),
                )
            )
        session.flush()
        assert system._prune_provider_health(session) == 5
        session.commit()
        rows = session.exec(
            select(ProviderHealth).where(
                ProviderHealth.provider == "provider-a"
            )
        ).all()
        assert len(rows) == 100


def test_liveness_remains_side_effect_free():
    payload = system.health()
    assert payload["status"] == "ok"
    assert payload["service"] == "groupbrief"
    assert payload["timestamp"]


def test_readiness_degrades_when_required_wechat_dependency_failed(tmp_path):
    client, api = _readiness_client(tmp_path)
    from app.db.models import Group

    api.state.startup_checks = [
        {"name": "WeChatDataAnalysis 数据源", "ok": False, "status": "UNAVAILABLE", "detail": "MCP offline"},
        {"name": "Codex GPT 群聊总结", "ok": True, "status": "OK", "detail": "ok"},
        {"name": "DeepSeek V4 Flash（备用）", "ok": False, "status": "UNAVAILABLE", "detail": "not configured"},
    ]
    override = api.dependency_overrides[repo.get_session]
    session_iterator = override()
    session = next(session_iterator)
    try:
        session.add(Group(display_name="群A", wechat_group_id="g1", enabled=True))
        session.commit()
    finally:
        session_iterator.close()

    with client:
        response = client.get("/api/system/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["wechat_data"]["status"] == "UNAVAILABLE"


def test_liveness_stays_ok_while_stale_scheduler_heartbeat_degrades_readiness(tmp_path):
    from datetime import datetime, timedelta

    from app.scheduler.heartbeat import record_scheduler_heartbeat

    client, api = _readiness_client(tmp_path)
    settings = api.dependency_overrides[get_settings]()
    settings.scheduler_owner = "fastapi"
    api.dependency_overrides[get_settings] = lambda: settings
    api.state.scheduler_owner = "fastapi"
    api.state.scheduler_active = True
    record_scheduler_heartbeat(
        settings,
        job="send_due",
        status="success",
        now=datetime.now().astimezone() - timedelta(minutes=10),
    )

    with client:
        live = client.get("/api/system/health")
        ready = client.get("/api/system/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"
    assert ready.json()["checks"]["scheduler_heartbeat"]["status"] == "STALE"


def test_startup_check_exception_is_preserved_as_explicit_failure(caplog):
    def fail(_settings):
        raise RuntimeError("startup probe crashed")

    with caplog.at_level(logging.ERROR, logger="app"):
        checks, error = _capture_startup_checks(object(), runner=fail)

    assert error == "startup probe crashed"
    assert checks == [
        {
            "name": "启动检查执行",
            "ok": False,
            "status": "ERROR",
            "detail": "startup probe crashed",
        }
    ]
    assert "启动检查执行失败" in caplog.text


def test_deep_health_formatting_reuses_existing_reports():
    summary = object.__new__(CodexGPTProvider)
    summary.model = "gpt-test"
    summary_report = {
        "ok": True,
        "version": {"value": "codex 1", "detail": "ok"},
        "fallback": {"configured": False},
    }
    assert summary.health_check(summary_report) == (
        True,
        "主模型 gpt-test（codex 1）；DeepSeek 备用未配置",
    )

    image = object.__new__(CodexImageGenerator)
    image_report = {
        "binary": {"ok": True},
        "version": {"ok": True, "value": "codex 1", "detail": "ok"},
        "last_image_smoke": {"ok": False},
    }
    assert image.health_check(image_report) == (
        True,
        "codex 可执行：codex 1；图片能力尚未实测",
    )

    sender = object.__new__(WechatNativeSender)
    sender.dry_run = False
    sender_report = {
        "ok": False,
        "dependencies": {"ok": True, "detail": "ok"},
        "desktop": {"ok": False, "detail": "桌面已锁定"},
        "ocr": {"ok": False, "detail": "未检查"},
        "clipboard": {"ok": False, "detail": "未检查"},
        "window": {"ok": False, "detail": "未检查"},
    }
    assert sender.health_check(sender_report) == (False, "桌面已锁定")


def test_recovery_endpoint_reuses_one_run_snapshot(monkeypatch):
    class CountingStore:
        def __init__(self):
            self.calls = 0

        def list_runs(self, _run_date=None):
            self.calls += 1
            return []

    store = CountingStore()
    monkeypatch.setattr(v2_ui, "_store", lambda _settings: store)

    assert v2_ui.recovery_info(settings=object()) == {
        "incomplete": [],
        "integrity": [],
    }
    assert store.calls == 1


def test_v2_startup_endpoint_returns_saved_snapshot_without_rerun():
    api = FastAPI()
    api.include_router(v2_ui.router)
    api.state.startup_checks = [
        {"name": "cached", "ok": True, "status": "OK", "detail": "saved"}
    ]
    api.state.startup_check_error = ""

    with TestClient(api) as client:
        response = client.get("/api/v2/system/startup")

    assert response.status_code == 200
    assert response.json() == {
        "checks": [
            {"name": "cached", "ok": True, "status": "OK", "detail": "saved"}
        ],
        "error": "",
    }
