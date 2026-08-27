"""生成 runtime/YYYY-MM-DD/status.json 的脱敏每日运行报告。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from app.v2.constants import SENT
from app.v2.run_store import RunStore, validate_run_date

_CHECKPOINT_ORDER = {
    "TASK_CREATED": 0,
    "MESSAGES_SAVED": 1,
    "RANKING_SAVED": 2,
    "PROMPT_SAVED": 3,
    "IMAGE_SAVED": 4,
    "SENT_CONFIRMED": 5,
}


def _scheduler_snapshot(store: RunStore, run_date: str) -> dict:
    path = store.root / ".scheduler" / f"{run_date}.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict) or parsed.get("run_date") != run_date:
        return {}
    return {
        key: parsed.get(key)
        for key in (
            "run_id",
            "generation_status",
            "generation_started_at",
            "generation_completed_at",
            "generation_invocation_completed_at",
            "email_status",
            "last_invocation_status",
            "last_invocation_exit_code",
        )
        if parsed.get(key) not in (None, "")
    }


def _step_status(run: dict, required_checkpoint: int, stage: str) -> str:
    checkpoint = _CHECKPOINT_ORDER.get(str(run.get("last_successful_checkpoint") or ""), 0)
    if checkpoint >= required_checkpoint:
        return "success"
    failed_stage = str(run.get("failed_stage") or "").lower()
    if failed_stage == stage:
        execution = str(run.get("execution_state") or "")
        if execution == "WAIT_RETRY":
            return "retry_pending"
        if execution == "HOLD_MANUAL":
            return "held"
        return "failed"
    return "pending"


def _group_snapshot(run: dict) -> dict:
    status = str(run.get("status") or "PENDING")
    send_state = str(run.get("send_state") or "")
    if status == SENT or run.get("sent_at"):
        send_status = "success"
    elif send_state == "failed_final":
        send_status = "held"
    elif run.get("send_next_retry_at"):
        send_status = "retry_pending"
    else:
        send_status = _step_status(run, 5, "send")
    return {
        "group_task_id": str(run.get("group_task_id") or ""),
        "group_id": str(run.get("group_id") or ""),
        "group_name": str(run.get("group_name") or ""),
        "run_status": status,
        "execution_state": str(run.get("execution_state") or ""),
        "last_successful_checkpoint": str(run.get("last_successful_checkpoint") or ""),
        "next_retry_at": str(run.get("next_retry_at") or ""),
        "retry_attempt_count": int(run.get("retry_attempt_count") or 0),
        "retry_budget": int(run.get("retry_budget") or 0),
        "data": {"status": _step_status(run, 1, "fetch")},
        "ranking": {"status": _step_status(run, 2, "ranking")},
        "summary": {
            "status": _step_status(run, 3, "prompt"),
            "model": str((run.get("prompt_meta") or {}).get("api_model") or "")
            if isinstance(run.get("prompt_meta"), dict)
            else "",
        },
        "prompt": {"status": _step_status(run, 3, "prompt")},
        "image": {
            "status": _step_status(run, 4, "image"),
            "attempts": int(run.get("image_attempt_count") or 0),
            "fallback_level": int(run.get("image_fallback_level") or 0),
        },
        "send": {
            "status": send_status,
            "state": send_state,
            "hold_reason": str(run.get("send_hold_reason") or ""),
            "next_retry_at": str(run.get("send_next_retry_at") or ""),
            "attempts": int(run.get("send_retry_attempt_count") or 0),
            "retry_budget": int(run.get("send_retry_budget") or 0),
        },
        "last_error_type": str(
            run.get("last_error_type") or run.get("error_type") or run.get("send_error_type") or ""
        ),
        "last_error_summary": str(
            run.get("last_error_summary") or run.get("error") or run.get("send_error") or ""
        )[:300],
        "updated_at": str(run.get("updated_at") or ""),
    }


def write_daily_status(store: RunStore, run_date: str) -> Path:
    run_date = validate_run_date(run_date)
    scheduler = _scheduler_snapshot(store, run_date)
    groups = [_group_snapshot(run) for run in store.list_runs(run_date)]
    groups.sort(key=lambda item: (item["group_id"], item["group_name"]))
    states = {item["execution_state"] for item in groups}
    if groups and all(item["run_status"] == SENT for item in groups):
        overall = "complete"
    elif "HOLD_MANUAL" in states or any(item["send"]["status"] == "held" for item in groups):
        overall = "attention_required"
    elif "WAIT_RETRY" in states:
        overall = "retry_pending"
    elif groups:
        overall = "in_progress"
    else:
        overall = "not_started"
    payload = {
        "schema_version": 1,
        "run_date": run_date,
        "run_id": str(scheduler.get("run_id") or f"groupbrief:{run_date}"),
        "updated_at": datetime.now().astimezone().isoformat(),
        "overall_status": overall,
        "scheduler": scheduler,
        "groups": groups,
    }
    runtime_root = store.root.parent / "runtime"
    path = runtime_root / run_date / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path
