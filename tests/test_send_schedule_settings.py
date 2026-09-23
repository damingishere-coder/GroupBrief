from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.api import settings as api
from app.config.settings import Settings
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler import manager


@pytest.mark.parametrize("value", ["", "24:00", "09:60", "9:30", "10:00:00", " 10:00"])
def test_invalid_send_time_does_not_write(monkeypatch, value):
    runtime = Settings(_env_file=None)
    monkeypatch.setattr(api, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(repo, "set_setting_value", lambda *args: pytest.fail("invalid write"))
    with pytest.raises(HTTPException) as error:
        api.update_settings(api.SettingsPayload(values={"schedule_send_time": value}), object())
    assert error.value.status_code == 422
    assert runtime.schedule_send_time == "08:30"


def test_save_reschedules_and_survives_restart(monkeypatch):
    runtime = Settings(_env_file=None, reliability_watchdog_enabled=False)
    repo.init_db(runtime)
    monkeypatch.setattr(api, "get_runtime_settings", lambda: runtime)
    scheduler = manager.start_scheduler(runtime)
    scheduler.pause()
    # A time already passed today must become tomorrow's trigger, without a send.
    now = datetime.now(ZoneInfo(runtime.app_timezone))
    selected = (now - timedelta(minutes=1)).strftime("%H:%M")
    try:
        with Session(repo.engine) as session:
            api.update_settings(api.SettingsPayload(values={"schedule_send_time": selected}), session)
            assert api.get_settings(session)["schedule_send_time"] == selected
        job = scheduler.get_job("daily_wechat_send_batch")
        assert job.next_run_time > now
        assert job.next_run_time.strftime("%H:%M") == selected
        assert len(scheduler.get_jobs()) == 2
        restored = Settings(_env_file=None)
        repo.apply_db_settings(restored)
        assert restored.schedule_send_time == selected
    finally:
        manager.stop_scheduler()
        with Session(repo.engine) as session:
            repo.set_setting_value(session, "schedule_send_time", "08:30")


def test_skipped_date_blocks_auto_recovery_and_manual_send(monkeypatch):
    runtime = Settings(_env_file=None, schedule_send_skip_dates="2026-09-23")
    pipeline = object.__new__(DailyPipeline)
    pipeline.settings = runtime
    monkeypatch.setattr(pipeline, "_load_groups", lambda: pytest.fail("must stop before loading groups"))
    monkeypatch.setattr(pipeline, "_sync_group_names_safe", lambda *args: pytest.fail("must not contact WeChat"))
    assert pipeline.send_due_for_dates(["2026-09-23"], recovery=True) == []
    assert pipeline.force_send(23, "2026-09-23", confirm_late_send=True, confirm_regenerated=True)["error_type"] == "USER_SKIPPED_SEND_DATE"
    assert runtime.is_send_skipped("2026-09-23")
    assert not runtime.is_send_skipped("2026-09-24")
    runtime.apply_runtime_values({"schedule_send_time": "10:00", "schedule_send_skip_dates": ""})
    assert runtime.is_send_skipped("2026-09-23")
