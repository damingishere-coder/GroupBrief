from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session

from app.config.settings import Settings
from app.db import repository as repo
from app.db.models import Group
from app.scheduler.recovery_planner import (
    RecoveryPlanChangedError,
    RecoveryPlanner,
    RecoverySelectionError,
)
from app.scheduler.daily_v2_job import (
    DailyScheduleState,
    ScheduleStateVersionConflictError,
)
from app.scheduler.period import PeriodResolver
from app.scheduler.task_manifest import manifest_fields
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


def test_daily_schedule_state_compare_and_update_is_cas(tmp_path):
    state_store = DailyScheduleState(tmp_path)
    state = state_store.update(
        "2026-08-29",
        **manifest_fields([]),
        generation_started_at="2026-08-29T00:15:00+08:00",
        generation_status="not_run",
        generation_completed_at="2026-08-29T00:15:01+08:00",
    )

    updated = state_store.compare_and_update(
        "2026-08-29",
        expected_state_version=state["state_version"],
        generation_status="running",
    )

    assert updated["state_version"] == state["state_version"] + 1
    with pytest.raises(ScheduleStateVersionConflictError, match="状态已变化"):
        state_store.compare_and_update(
            "2026-08-29",
            expected_state_version=state["state_version"],
            generation_status="running",
        )


def test_repair_empty_manifest_rejects_stale_version_before_generation(tmp_path):
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
    planner = _planner(settings, tmp_path)
    planner.state_store.update(
        "2026-08-29",
        **manifest_fields([]),
        generation_started_at="2026-08-29T00:15:00+08:00",
        generation_status="not_run",
        generation_completed_at="2026-08-29T00:15:01+08:00",
    )

    with pytest.raises(RecoveryPlanChangedError, match="版本已变化"):
        planner.repair_empty_manifest_and_generate(
            "2026-08-29",
            expected_state_version=99,
            expected_group_ids=[group.id],
        )


def test_repair_empty_manifest_rebuilds_manifest_and_generates_only(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    repo.init_db(settings)
    with Session(repo.engine) as session:
        group = repo.save_group(
            session,
            Group(
                display_name="恢复群",
                wechat_group_id="recover@chatroom",
                schedule_rule="daily_previous_day",
                wechat_send_enabled=True,
            ),
        )
    planner = _planner(settings, tmp_path)
    state = planner.state_store.update(
        "2026-08-29",
        **manifest_fields([]),
        generation_started_at="2026-08-29T00:15:00+08:00",
        generation_status="not_run",
        generation_completed_at="2026-08-29T00:15:01+08:00",
    )
    calls = []

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings
            self.period_resolver = PeriodResolver()

        def generate_all(self, **kwargs):
            calls.append(kwargs)
            return [
                {
                    "group_id": group.id,
                    "group_name": "恢复群",
                    "status": "ready_to_send",
                }
            ]

    monkeypatch.setattr("app.scheduler.recovery_planner.DailyPipeline", FakePipeline)
    result = planner.repair_empty_manifest_and_generate(
        "2026-08-29",
        expected_state_version=state["state_version"],
        expected_group_ids=[group.id],
    )

    repaired = planner.state_store.load("2026-08-29")
    assert result["status"] == "success"
    assert result["generation_only"] is True
    assert result["send_invoked"] is False
    assert repaired["expected_group_count"] == 1
    assert repaired["expected_groups"][0]["schedule_rule"] == "daily_previous_day"
    assert repaired["expected_groups"][0]["expected_terminal"] == "SENT"
    assert repaired["generation_status"] == "success"
    assert repaired["email_status"] == "skipped_by_recovery_request"
    assert repaired["manifest_source"] == "manual_empty_manifest_repair_current_config"
    assert calls[0]["group_ids"] == [group.id]
    assert calls[0]["acquire_lock"] is False


def test_repair_empty_manifest_rejects_existing_group_run(tmp_path):
    settings = _settings(tmp_path)
    repo.init_db(settings)
    with Session(repo.engine) as session:
        group = repo.save_group(
            session,
            Group(display_name="恢复群", wechat_group_id="recover@chatroom"),
        )
    planner = _planner(settings, tmp_path)
    state = planner.state_store.update(
        "2026-08-29",
        **manifest_fields([]),
        generation_started_at="2026-08-29T00:15:00+08:00",
        generation_status="not_run",
        generation_completed_at="2026-08-29T00:15:01+08:00",
    )
    planner.store.save_run(
        "恢复群",
        "2026-08-29",
        {"group_id": str(group.id), "status": "PENDING"},
    )

    with pytest.raises(RecoverySelectionError, match="已存在群级运行记录"):
        planner.repair_empty_manifest_and_generate(
            "2026-08-29",
            expected_state_version=state["state_version"],
            expected_group_ids=[group.id],
        )
