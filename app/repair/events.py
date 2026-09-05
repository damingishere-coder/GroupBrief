"""从权威脱敏状态生成 RepairIncident；不读取消息、Prompt 或认证文件。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.config.settings import Settings
from app.repair.store import RepairIncidentStore
from app.scheduler.daily_v2_job import DailyScheduleState
from app.v2.run_store import RunStore
from app.weekly.store import WeeklyStore


def _opaque_group(run: dict) -> str:
    value = str(run.get("group_id") or run.get("group_name") or "unknown")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def capture_daily_incidents(
    settings: Settings,
    run_store: RunStore,
    run_date: str,
) -> list[dict]:
    incidents: list[dict] = []
    ledger = RepairIncidentStore(settings)
    for run in run_store.list_runs(run_date):
        error_type = str(run.get("error_type") or run.get("send_error_type") or "")
        if not error_type:
            continue
        stage = str(run.get("failed_stage") or run.get("stage") or "unknown").lower()
        scope = "send" if stage == "send" or error_type.startswith("SEND_") else stage
        incidents.append(
            ledger.record(
                scope=scope,
                error_type=error_type,
                stage=stage,
                source_path=f"daily/{run_date}/group-{_opaque_group(run)}/run.json",
                error_summary=str(run.get("error") or run.get("send_error") or ""),
            )
        )
    scheduler_state = DailyScheduleState(settings.output_dir).load(run_date)
    if scheduler_state.get("state_status") == "corrupt":
        incidents.append(
            ledger.record(
                scope="scheduler",
                error_type="SCHEDULER_STATE_CORRUPT",
                stage="state",
                source_path=f".scheduler/{run_date}.json",
                error_summary=str(scheduler_state.get("state_error_reason") or ""),
            )
        )
    return incidents


def capture_weekly_incidents(settings: Settings) -> list[dict]:
    incidents: list[dict] = []
    ledger = RepairIncidentStore(settings)
    for state in WeeklyStore(settings.output_dir).list_states():
        error_type = str(state.get("error_type") or "")
        if not error_type:
            continue
        group_id = int(state.get("group_id") or 0)
        source = (
            f".weekly/{state.get('week_start', '')}_{state.get('week_end', '')}/"
            f"group-{group_id}/weekly.json"
        )
        incidents.append(
            ledger.record(
                scope="weekly",
                error_type=error_type,
                stage=str(state.get("stage") or "weekly"),
                source_path=source,
                error_summary=str(state.get("error_summary") or state.get("send_error") or ""),
            )
        )
    return incidents


def capture_persisted_incidents(settings: Settings) -> list[dict]:
    store = RunStore(settings.output_dir)
    dates = sorted(
        {
            str(run.get("run_date") or "")
            for run in store.list_runs()
            if str(run.get("run_date") or "")
        },
        reverse=True,
    )[:2]
    incidents: list[dict] = []
    for run_date in dates:
        incidents.extend(capture_daily_incidents(settings, store, run_date))
    incidents.extend(capture_weekly_incidents(settings))
    return incidents
