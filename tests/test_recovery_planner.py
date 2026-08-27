from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session

from app.config.settings import Settings
from app.db import repository as repo
from app.db.models import Group
from app.scheduler.recovery_planner import RecoveryPlanChangedError, RecoveryPlanner
from app.scheduler.daily_v2_job import DailyScheduleState
from app.v2.run_store import RunStore


def _settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}",
        reliability_lookback_days=2,
    )


def _planner(settings, tmp_path):
    store = RunStore(tmp_path / "output")
    return RecoveryPlanner(
        settings,
        store=store,
        state_store=DailyScheduleState(store.root),
    )


def test_preview_lists_old_missing_tasks_without_invoking_generation(tmp_path):
    settings = _settings(tmp_path)
    repo.init_db(settings)
    with Session(repo.engine) as session:
        repo.save_group(
            session,
            Group(
                display_name="恢复群",
                wechat_group_id="recover@chatroom",
                schedule_rule="daily_previous_day",
                image_enabled=True,
            ),
        )

    planner = _planner(settings, tmp_path)
    preview = planner.preview(
        now=datetime(2026, 8, 27, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        lookback_days=4,
    )

    assert preview["automatic_recovery_dates"] == ["2026-08-26", "2026-08-27"]
    assert [item["run_date"] for item in preview["items"]] == ["2026-08-24", "2026-08-25"]
    assert all(item["safe_stage"] == "generation_only" for item in preview["items"])
    assert len(preview["version"]) == 64
    assert not list(planner.store.root.rglob("run.json"))


def test_confirm_rejects_stale_version_before_generation(tmp_path):
    settings = _settings(tmp_path)
    repo.init_db(settings)
    with Session(repo.engine) as session:
        group = repo.save_group(
            session,
            Group(
                display_name="恢复群",
                wechat_group_id="recover@chatroom",
                schedule_rule="daily_previous_day",
            ),
        )

    with pytest.raises(RecoveryPlanChangedError, match="已变化"):
        _planner(settings, tmp_path).confirm_generation(
            [{"run_date": "2026-08-24", "group_id": group.id}],
            expected_version="0" * 64,
            now=datetime(2026, 8, 27, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
