"""历史恢复预览与显式生成确认。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.daily_v2_job import DailyScheduleState, _generation_status
from app.scheduler.reliability_watchdog import recovery_dates
from app.scheduler.runtime_status import write_daily_status
from app.scheduler.task_manifest import build_expected_groups, manifest_fields
from app.services.generation_runtime import generation_mutex
from app.v2.constants import IMAGE_READY, READY_TO_SEND, SENT
from app.v2.run_store import RunStore, validate_run_date

_TERMINAL_GENERATION_STATUSES = {IMAGE_READY, READY_TO_SEND, SENT}


class RecoveryPlanChangedError(RuntimeError):
    """确认前 backlog 已改变。"""


class RecoverySelectionError(ValueError):
    """选择的任务不允许自动恢复。"""


def _is_fail_closed(run: dict) -> bool:
    values = (
        run.get("execution_state"),
        run.get("error_type"),
        run.get("last_error_type"),
        run.get("send_hold_reason"),
        run.get("prompt_hold_reason"),
    )
    text = "|".join(str(value or "").upper() for value in values)
    return "UNKNOWN" in text or "CORRUPT" in text


def _version_for(items: list[dict]) -> str:
    payload = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecoveryPlanner:
    def __init__(
        self,
        settings: Settings,
        *,
        store: RunStore | None = None,
        state_store: DailyScheduleState | None = None,
    ):
        self.settings = settings
        self.store = store or RunStore(settings.output_dir)
        self.state_store = state_store or DailyScheduleState(self.store.root)

    def preview(
        self,
        *,
        now: datetime | None = None,
        lookback_days: int = 30,
    ) -> dict:
        tz = ZoneInfo(self.settings.app_timezone)
        now = now or datetime.now(tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        days = min(max(int(lookback_days), 3), 30)
        start = now.date() - timedelta(days=days - 1)
        automatic_dates = set(
            recovery_dates(now, self.settings.reliability_lookback_days)
        )
        groups = self._load_enabled_groups()
        current_by_id = {int(group.id): group for group in groups if group.id is not None}
        items: list[dict] = []
        for offset in range(days):
            run_date = (start + timedelta(days=offset)).isoformat()
            if run_date in automatic_dates:
                continue
            state = self.state_store.load(run_date)
            if state.get("state_status") == "corrupt":
                items.append(
                    {
                        "run_date": run_date,
                        "group_id": None,
                        "group_name": "调度状态",
                        "status": "CORRUPT",
                        "execution_state": "HOLD_MANUAL",
                        "reason": "SCHEDULER_STATE_CORRUPT",
                        "safe_stage": "manual_review_only",
                        "recoverable": False,
                        "manifest_source": "corrupt",
                        "updated_at": "",
                    }
                )
                continue
            manifest = state.get("expected_groups")
            if isinstance(manifest, list):
                expected = [row for row in manifest if isinstance(row, dict)]
                source = "recorded_manifest"
            else:
                expected = build_expected_groups(
                    groups,
                    datetime.fromisoformat(run_date).date(),
                    timezone=self.settings.app_timezone,
                )
                source = "current_config_preview"
            runs = {
                str(run.get("group_id") or ""): run
                for run in self.store.list_runs(run_date)
                if isinstance(run, dict)
            }
            for snapshot in expected:
                group_id = snapshot.get("group_id")
                if not isinstance(group_id, int) or group_id <= 0:
                    continue
                run = runs.get(str(group_id), {})
                status = str(run.get("status") or "MISSING")
                if status in _TERMINAL_GENERATION_STATUSES:
                    continue
                fail_closed = _is_fail_closed(run)
                current_group = current_by_id.get(group_id)
                recoverable = current_group is not None and not fail_closed
                reason = (
                    "RESULT_UNKNOWN_OR_CORRUPT"
                    if fail_closed
                    else "GROUP_NOT_ACTIVE"
                    if current_group is None
                    else str(run.get("error_type") or "MISSING_GENERATION")
                )
                items.append(
                    {
                        "run_date": run_date,
                        "group_id": group_id,
                        "group_name": str(snapshot.get("group_name") or ""),
                        "status": status,
                        "execution_state": str(run.get("execution_state") or ""),
                        "reason": reason,
                        "safe_stage": "generation_only" if recoverable else "manual_review_only",
                        "recoverable": recoverable,
                        "manifest_source": source,
                        "estimated_summary_calls": 1 if recoverable else 0,
                        "estimated_image_calls": (
                            1 if recoverable and bool(snapshot.get("image_enabled", True)) else 0
                        ),
                        "updated_at": str(run.get("updated_at") or state.get("updated_at") or ""),
                    }
                )
        items.sort(key=lambda item: (item["run_date"], item.get("group_id") or 0))
        return {
            "generated_at": now.isoformat(),
            "automatic_recovery_dates": sorted(automatic_dates),
            "lookback_days": days,
            "version": _version_for(items),
            "items": items,
        }

    def confirm_generation(
        self,
        selections: list[dict],
        *,
        expected_version: str,
        now: datetime | None = None,
    ) -> dict:
        preview = self.preview(now=now)
        if not expected_version or expected_version != preview["version"]:
            raise RecoveryPlanChangedError("恢复清单已变化，请刷新后重新确认")
        allowed = {
            (item["run_date"], item["group_id"]): item
            for item in preview["items"]
            if item.get("recoverable") and item.get("group_id") is not None
        }
        normalized: list[tuple[str, int]] = []
        for selection in selections:
            run_date = validate_run_date(str(selection.get("run_date") or ""))
            group_id = selection.get("group_id")
            if not isinstance(group_id, int) or (run_date, group_id) not in allowed:
                raise RecoverySelectionError("所选任务已不可恢复或需要人工复核")
            normalized.append((run_date, group_id))
        normalized = sorted(set(normalized))
        if not normalized:
            raise RecoverySelectionError("至少选择一个可恢复任务")

        results: list[dict] = []
        with generation_mutex():
            repo.init_db(self.settings)
            repo.apply_db_settings(self.settings)
            pipeline = DailyPipeline(settings=self.settings)
            groups = self._load_enabled_groups()
            by_id = {int(group.id): group for group in groups if group.id is not None}
            for run_date in sorted({item[0] for item in normalized}):
                ids = [group_id for date_value, group_id in normalized if date_value == run_date]
                selected_groups = [by_id[group_id] for group_id in ids if group_id in by_id]
                expected = build_expected_groups(
                    selected_groups,
                    datetime.fromisoformat(run_date).date(),
                    timezone=self.settings.app_timezone,
                    resolver=pipeline.period_resolver,
                )
                state = self.state_store.load(run_date)
                fields = {
                    "manual_recovery_confirmed_at": datetime.now().astimezone().isoformat(),
                    "manual_recovery_generation_only": True,
                }
                if not isinstance(state.get("expected_groups"), list):
                    # 这份清单只为人工确认的历史生成而创建；接口不承担历史发送，
                    # 因此不能把新建清单的期望终态写成 SENT。
                    for row in expected:
                        row["expected_terminal"] = "READY_TO_SEND"
                        row["wechat_send_enabled"] = False
                    fields.update(manifest_fields(expected))
                    fields["manifest_source"] = "manual_selection"
                state = self.state_store.update(run_date, **fields)
                day_results = pipeline.generate_all(
                    run_date=run_date,
                    group_ids=ids,
                    group_overrides={
                        int(row["group_id"]): row
                        for row in state.get("expected_groups", [])
                        if isinstance(row, dict) and row.get("group_id") in ids
                    },
                    acquire_lock=False,
                )
                self.state_store.update(
                    run_date,
                    manual_recovery_results=day_results,
                    manual_recovery_status=_generation_status(day_results),
                )
                write_daily_status(self.store, run_date)
                results.extend(day_results)
        return {
            "status": _generation_status(results),
            "generation_only": True,
            "send_invoked": False,
            "results": results,
        }

    def _load_enabled_groups(self):
        from sqlmodel import Session

        repo.init_db(self.settings)
        with Session(repo.engine) as session:
            return repo.list_groups(session, only_enabled=True)
