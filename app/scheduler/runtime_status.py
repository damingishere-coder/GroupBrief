"""生成 runtime/YYYY-MM-DD/status.json 的脱敏每日运行报告。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.image.delivery_guard import image_delivery_eligible, image_fallback_level
from app.v2.constants import (
    CORRUPT,
    EXECUTION_ACTIVE,
    EXECUTION_COMPLETE,
    EXECUTION_FAILED_FINAL,
    EXECUTION_HOLD_MANUAL,
    EXECUTION_WAIT_RETRY,
    IMAGE_READY,
    READY_TO_SEND,
    SENT,
)
from app.v2.run_store import RunStore, validate_run_date

_CHECKPOINT_ORDER = {
    "TASK_CREATED": 0,
    "MESSAGES_SAVED": 1,
    "RANKING_SAVED": 2,
    "PROMPT_SAVED": 3,
    "IMAGE_SAVED": 4,
    "SENT_CONFIRMED": 5,
}

_NODE_DEFINITIONS = (
    ("scheduler", "调度启动", 0, ""),
    ("data", "读取群消息", 1, "fetch"),
    ("ranking", "生成排行榜", 2, "ranking"),
    ("prompt", "摘要与提示词", 3, "prompt"),
    ("image", "生成图片", 4, "image"),
    ("send", "等待发送 / 发送完成", 5, "send"),
)

_NODE_LABELS = {node_id: label for node_id, label, _, _ in _NODE_DEFINITIONS}
_STAGE_NODE = {
    "DATA": "data",
    "RANKING": "ranking",
    "PROMPT": "prompt",
    "IMAGE": "image",
    "SEND": "send",
    "COMPLETE": "send",
}
_ACTIVE_SCHEDULER_STATUSES = {"running", "resuming"}


def _scheduler_snapshot(output_root: Path, run_date: str) -> dict:
    path = output_root / ".scheduler" / f"{run_date}.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        if path.exists():
            return {
                "state_status": "corrupt",
                "error_type": "SCHEDULER_STATE_CORRUPT",
                "state_error_reason": "read_or_json_invalid",
                "generation_hold": True,
            }
        return {}
    if not isinstance(parsed, dict) or parsed.get("run_date") != run_date:
        return {
            "state_status": "corrupt",
            "error_type": "SCHEDULER_STATE_CORRUPT",
            "state_error_reason": "run_date_or_root_invalid",
            "generation_hold": True,
        }
    return {
        key: parsed.get(key)
        for key in (
            "run_id",
            "generation_status",
            "generation_started_at",
            "generation_completed_at",
            "generation_invocation_completed_at",
            "last_invocation_status",
            "last_invocation_exit_code",
            "manifest_version",
            "manifest_created_at",
            "expected_group_count",
            "expected_groups",
            "state_version",
            "state_status",
            "error_type",
            "state_error_reason",
            "generation_hold",
            "generation_error",
            "email_started_at",
            "email_completed_at",
            "email_status",
        )
        if parsed.get(key) not in (None, "")
    }


def _step_status(run: dict, required_checkpoint: int, stage: str) -> str:
    checkpoint = _CHECKPOINT_ORDER.get(str(run.get("last_successful_checkpoint") or ""), 0)
    if checkpoint >= required_checkpoint:
        return "success"
    failed_stage = str(run.get("failed_stage") or "").lower()
    if failed_stage == stage or (stage == "fetch" and failed_stage == "data"):
        return _failure_status(run)
    return "pending"


def _failure_status(run: dict) -> str:
    execution = str(run.get("execution_state") or "")
    send_state = str(run.get("send_state") or "")
    if (
        execution == EXECUTION_HOLD_MANUAL
        or run.get("prompt_hold")
        or run.get("send_hold")
        or send_state in {"unknown", "failed_final"}
        or str(run.get("status") or "") == CORRUPT
    ):
        return "held"
    if execution == EXECUTION_WAIT_RETRY or run.get("next_retry_at") or run.get("send_next_retry_at"):
        return "retry_pending"
    return "failed"


def _run_has_started(run: dict) -> bool:
    return bool(
        run.get("updated_at")
        or run.get("group_task_id")
        or run.get("period_start")
        or run.get("prompt_operation_started_at")
        or run.get("sent_at")
    )


def _current_node_id(run: dict) -> str:
    failed_stage = str(run.get("failed_stage") or "").lower()
    if failed_stage in {"data", "fetch"}:
        return "data"
    if failed_stage in {"ranking", "prompt", "image", "send"}:
        return failed_stage
    stage = str(run.get("stage") or "").upper()
    if stage in _STAGE_NODE:
        return _STAGE_NODE[stage]
    status = str(run.get("status") or "PENDING")
    return {
        "PENDING": "data",
        "DATA_READY": "ranking",
        "RANKING_READY": "prompt",
        "PROMPT_READY": "image",
        "IMAGE_READY": "send",
        "READY_TO_SEND": "send",
        "SENT": "send",
        "FAILED": failed_stage if failed_stage in _NODE_LABELS else "data",
        "CORRUPT": "data",
    }.get(status, "data")


def _group_node_status(
    run: dict,
    node_id: str,
    required_checkpoint: int,
    stage: str,
    *,
    scheduler_active: bool,
    scheduler_started: bool,
) -> str:
    if node_id == "scheduler":
        return "success" if scheduler_started else "pending"
    image_job = run.get("image_job") if isinstance(run.get("image_job"), dict) else {}
    image_attempt_finished = bool(
        run.get("image_status")
        or image_job.get("status") in {"completed", "failed", "ambiguous_result", "diagnostic_fallback"}
        or str(run.get("status") or "") in {"IMAGE_READY", "READY_TO_SEND", "SENT"}
    )
    if node_id == "image" and image_attempt_finished and not image_delivery_eligible(run):
        # 历史 SENT 仍保持发送成功，但图片节点必须呈现诊断失败事实。
        return "failed"

    status = _step_status(run, required_checkpoint, stage)
    if status != "pending":
        return status

    if node_id == "send":
        send_state = str(run.get("send_state") or "")
        if send_state in {"claimed", "sending_text", "sending_image"}:
            return "running"
        if send_state in {"unknown", "failed_final"} or run.get("send_hold"):
            return "held"
        if run.get("send_next_retry_at"):
            return "retry_pending"

    if _current_node_id(run) != node_id or not _run_has_started(run):
        return "pending"
    if node_id == "image":
        image_job = run.get("image_job")
        image_job_status = (
            str(image_job.get("status") or "") if isinstance(image_job, dict) else ""
        )
        if image_job_status == "queued":
            return "pending"
        if image_job_status in {"running", "started"}:
            return "running"
    if node_id == "prompt" and str(run.get("prompt_operation_status") or "") == "started":
        return "running"
    if scheduler_active and str(run.get("execution_state") or EXECUTION_ACTIVE) == EXECUTION_ACTIVE:
        return "running"
    return "pending"


def _aggregate_node_status(statuses: list[str]) -> str:
    if not statuses:
        return "pending"
    for candidate in ("held", "failed", "retry_pending", "running"):
        if candidate in statuses:
            return candidate
    if all(status == "success" for status in statuses):
        return "success"
    return "pending"


def _scheduled_at(run_date: str, clock_time: str, timezone: str) -> str:
    try:
        hour, minute = (int(part) for part in clock_time.split(":", 1))
        value = datetime.fromisoformat(run_date).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
            tzinfo=ZoneInfo(timezone),
        )
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return ""
    return value.isoformat()


def _next_scheduled_at(clock_time: str, timezone: str) -> str:
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        hour, minute = (int(part) for part in clock_time.split(":", 1))
        value = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if value <= now:
            value += timedelta(days=1)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return ""
    return value.isoformat()


def _group_snapshot(
    run: dict,
    *,
    scheduler_active: bool = False,
    scheduler_started: bool = False,
) -> dict:
    status = str(run.get("status") or "PENDING")
    send_state = str(run.get("send_state") or "")
    if status == SENT or run.get("sent_at"):
        send_status = "success"
    elif send_state in {"unknown", "failed_final"} or run.get("send_hold"):
        send_status = "held"
    elif run.get("send_next_retry_at"):
        send_status = "retry_pending"
    elif send_state in {"claimed", "sending_text", "sending_image"}:
        send_status = "running"
    else:
        send_status = _step_status(run, 5, "send")

    node_items = [
        {
            "id": node_id,
            "label": label,
            "status": _group_node_status(
                run,
                node_id,
                required_checkpoint,
                stage,
                scheduler_active=scheduler_active,
                scheduler_started=scheduler_started,
            ),
        }
        for node_id, label, required_checkpoint, stage in _NODE_DEFINITIONS
    ]
    current_node = _current_node_id(run)
    current_status = next(
        (item["status"] for item in node_items if item["id"] == current_node),
        "pending",
    )
    execution = str(run.get("execution_state") or "")
    if status == SENT or execution == EXECUTION_COMPLETE:
        current_status = "success"
    elif status == CORRUPT:
        current_status = "held"
    elif execution == EXECUTION_FAILED_FINAL:
        current_status = "failed"
    elif execution == EXECUTION_HOLD_MANUAL:
        current_status = "held"
    elif execution == EXECUTION_WAIT_RETRY:
        current_status = "retry_pending"
    if current_status in {"held", "failed", "retry_pending"}:
        for item in node_items:
            if item["id"] == current_node:
                item["status"] = current_status
                break

    return {
        "group_task_id": str(run.get("group_task_id") or ""),
        "group_id": str(run.get("group_id") or ""),
        "group_name": str(run.get("group_name") or ""),
        "run_status": status,
        "execution_state": execution,
        "has_started": _run_has_started(run),
        "current_node": current_node,
        "current_node_label": _NODE_LABELS.get(current_node, "读取群消息"),
        "node_status": current_status,
        "nodes": node_items,
        "last_successful_checkpoint": str(run.get("last_successful_checkpoint") or ""),
        "next_retry_at": str(run.get("next_retry_at") or ""),
        "retry_attempt_count": int(run.get("retry_attempt_count") or 0),
        "retry_budget": int(run.get("retry_budget") or 0),
        "data": {
            "status": next(item["status"] for item in node_items if item["id"] == "data")
        },
        "ranking": {
            "status": next(item["status"] for item in node_items if item["id"] == "ranking")
        },
        "summary": {
            "status": next(item["status"] for item in node_items if item["id"] == "prompt"),
            "model": str((run.get("prompt_meta") or {}).get("api_model") or "")
            if isinstance(run.get("prompt_meta"), dict)
            else "",
        },
        "prompt": {
            "status": next(item["status"] for item in node_items if item["id"] == "prompt")
        },
        "image": {
            "status": next(item["status"] for item in node_items if item["id"] == "image"),
            "job_status": str((run.get("image_job") or {}).get("status") or "")
            if isinstance(run.get("image_job"), dict)
            else "",
            "attempts": int(run.get("image_attempt_count") or 0),
            "fallback_level": image_fallback_level(run),
            "fallback_reason": str(run.get("image_fallback_reason") or ""),
            "variant": str(run.get("image_variant") or "normal"),
            "delivery_eligible": image_delivery_eligible(run),
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
            run.get("last_error_type")
            or run.get("error_type")
            or run.get("send_error_type")
            or run.get("prompt_hold_reason")
            or run.get("send_hold_reason")
            or ""
        ),
        "last_error_summary": str(
            run.get("last_error_summary")
            or run.get("error")
            or run.get("prompt_original_error")
            or run.get("send_error")
            or run.get("prompt_operation_error")
            or run.get("prompt_hold_reason")
            or run.get("send_hold_reason")
            or ""
        )[:300],
        "updated_at": str(run.get("updated_at") or ""),
    }


def build_daily_status(
    store: RunStore,
    run_date: str,
    *,
    runs: Iterable[dict] | None = None,
    output_root: Path | None = None,
    schedule_generate_time: str = "00:15",
    schedule_send_time: str = "08:30",
    app_timezone: str = "Asia/Shanghai",
) -> dict:
    """只读构建每日运行投影；不会写回 scheduler 或 run.json。"""

    run_date = validate_run_date(run_date)
    resolved_output_root = Path(output_root) if output_root is not None else store.root
    scheduler = _scheduler_snapshot(resolved_output_root, run_date)
    scheduler_status = str(scheduler.get("generation_status") or "").lower()
    scheduler_active = scheduler_status in _ACTIVE_SCHEDULER_STATUSES
    scheduler_started = bool(
        scheduler.get("generation_started_at")
        or scheduler.get("generation_completed_at")
        or scheduler_active
    )
    selected_runs = runs if runs is not None else store.list_runs(run_date)
    raw_runs = [dict(run) for run in selected_runs if isinstance(run, dict)]
    groups = [
        _group_snapshot(
            run,
            scheduler_active=scheduler_active,
            scheduler_started=scheduler_started,
        )
        for run in raw_runs
    ]
    groups.sort(key=lambda item: (item["group_id"], item["group_name"]))
    states = {item["execution_state"] for item in groups if item["has_started"]}
    expected_rows = scheduler.get("expected_groups")
    has_manifest = isinstance(expected_rows, list)
    expected_rows = expected_rows if has_manifest else []
    expected_by_id = {
        str(item.get("group_id")): item
        for item in expected_rows
        if isinstance(item, dict) and item.get("group_id") is not None
    }
    actual_by_id = {
        item["group_id"]: item
        for item in groups
        if item["group_id"] and item["has_started"]
    }
    missing_expected_ids = sorted(set(expected_by_id) - set(actual_by_id))

    def reached_expected(group_id: str, item: dict) -> bool:
        expected_terminal = str(
            (expected_by_id.get(group_id) or {}).get("expected_terminal") or ""
        )
        status = item["run_status"]
        if expected_terminal == SENT:
            return status == SENT
        return status in {READY_TO_SEND, IMAGE_READY, SENT}

    completed_count = sum(
        1 for group_id, item in actual_by_id.items() if reached_expected(group_id, item)
    )
    retry_count = sum(1 for item in groups if item["execution_state"] == "WAIT_RETRY")
    manual_count = sum(
        1
        for item in groups
        if item["execution_state"] == "HOLD_MANUAL" or item["send"]["status"] == "held"
    )
    external_call_count = sum(
        int(item.get("external_call_count") or 0)
        for item in raw_runs
    )
    actual_providers = sorted(
        {
            str(item.get(field) or "").strip()
            for item in raw_runs
            for field in ("summary_provider_actual", "prompt_provider_actual")
            if str(item.get(field) or "").strip()
        }
    )
    started_count = sum(1 for item in groups if item["has_started"])
    failed_count = sum(
        1
        for item in groups
        if item["execution_state"] == EXECUTION_FAILED_FINAL or item["node_status"] == "failed"
    )
    corrupt = scheduler.get("state_status") == "corrupt" or any(
        item["run_status"] == CORRUPT for item in groups
    )
    any_group_running = any(item["node_status"] == "running" for item in groups)

    if corrupt:
        overall = "needs_attention"
    elif scheduler_active or any_group_running:
        overall = "running"
    elif manual_count:
        overall = "blocked"
    elif retry_count or EXECUTION_WAIT_RETRY in states:
        overall = "retry_pending"
    elif failed_count:
        overall = "failed"
    elif not has_manifest and (started_count or scheduler_started):
        overall = "needs_attention"
    elif missing_expected_ids:
        overall = "needs_attention"
    elif expected_by_id and completed_count == len(expected_by_id):
        overall = "complete"
    elif completed_count or (started_count and scheduler.get("generation_completed_at")):
        overall = "partial"
    elif started_count or scheduler_started:
        overall = "running"
    else:
        overall = "not_started"

    configured_group_count = max(len(groups), len(expected_by_id))
    scheduler_node_status = "pending"
    if scheduler.get("state_status") == "corrupt" or scheduler.get("generation_hold"):
        scheduler_node_status = "held"
    elif scheduler_started:
        scheduler_node_status = "running" if scheduler_active and not started_count else "success"

    nodes = [
        {
            "id": "scheduler",
            "label": _NODE_LABELS["scheduler"],
            "status": scheduler_node_status,
            "completed_groups": configured_group_count if scheduler_started else 0,
            "total_groups": configured_group_count,
        }
    ]
    for node_id, label, _, _ in _NODE_DEFINITIONS[1:]:
        statuses = [
            next(node["status"] for node in group["nodes"] if node["id"] == node_id)
            for group in groups
        ]
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "status": _aggregate_node_status(statuses),
                "completed_groups": sum(1 for status in statuses if status == "success"),
                "total_groups": configured_group_count,
            }
        )

    scheduler = {
        **scheduler,
        "scheduled_at": _scheduled_at(
            run_date,
            schedule_generate_time,
            app_timezone,
        ),
        "send_scheduled_at": _scheduled_at(
            run_date,
            schedule_send_time,
            app_timezone,
        ),
        "next_generate_at": _next_scheduled_at(
            schedule_generate_time,
            app_timezone,
        ),
        "next_send_at": _next_scheduled_at(
            schedule_send_time,
            app_timezone,
        ),
    }
    payload = {
        "schema_version": 2,
        "run_date": run_date,
        "run_id": str(scheduler.get("run_id") or f"groupbrief:{run_date}"),
        "updated_at": datetime.now().astimezone().isoformat(),
        "overall_status": overall,
        "scheduler": scheduler,
        "summary": {
            "expected_group_count": len(expected_by_id),
            "discovered_group_count": len(actual_by_id),
            "configured_group_count": configured_group_count,
            "completed_group_count": completed_count,
            "retry_group_count": retry_count,
            "manual_group_count": manual_count,
            "missing_expected_group_ids": missing_expected_ids,
            "manifest_complete": has_manifest and not missing_expected_ids,
            "external_call_count": external_call_count,
            "actual_providers": actual_providers,
        },
        "nodes": nodes,
        "groups": groups,
    }
    return payload


def write_daily_status(store: RunStore, run_date: str) -> Path:
    run_date = validate_run_date(run_date)
    payload = build_daily_status(store, run_date)
    runtime_root = store.root.parent / "runtime"
    path = runtime_root / run_date / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path
