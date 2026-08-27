"""V2 逐群任务的可靠性元数据与自动重试判定。

阶段状态继续使用 ``PENDING -> ... -> SENT/FAILED``；本模块只增加正交的
execution_state、checkpoint 和 retry 元数据，使旧 run.json 可以惰性升级，
同时不会把结果未知的外部调用误判为可自动重试。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from app.v2.constants import (
    CORRUPT,
    DATA_READY,
    DEFAULT_RETRY_BUDGET,
    EXECUTION_ACTIVE,
    EXECUTION_COMPLETE,
    EXECUTION_FAILED_FINAL,
    EXECUTION_HOLD_MANUAL,
    EXECUTION_WAIT_RETRY,
    FAILED,
    IMAGE_FILE_MISSING,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    MESSAGE_FETCH_FAILED,
    PENDING,
    PROMPT_FAILED,
    PROMPT_READY,
    RANKING_FAILED,
    RANKING_READY,
    READY_TO_SEND,
    SENT,
    WECHAT_DATA_UNAVAILABLE,
)

CHECKPOINT_BY_STATUS = {
    PENDING: "TASK_CREATED",
    DATA_READY: "MESSAGES_SAVED",
    RANKING_READY: "RANKING_SAVED",
    PROMPT_READY: "PROMPT_SAVED",
    IMAGE_READY: "IMAGE_SAVED",
    READY_TO_SEND: "IMAGE_SAVED",
    SENT: "SENT_CONFIRMED",
}

STAGE_BY_STATUS = {
    PENDING: "DATA",
    DATA_READY: "RANKING",
    RANKING_READY: "PROMPT",
    PROMPT_READY: "IMAGE",
    IMAGE_READY: "SEND",
    READY_TO_SEND: "SEND",
    SENT: "COMPLETE",
    FAILED: "FAILED",
    CORRUPT: "STATE",
}

_RETRYABLE_ERROR_TYPES = frozenset(
    {
        WECHAT_DATA_UNAVAILABLE,
        MESSAGE_FETCH_FAILED,
        RANKING_FAILED,
        PROMPT_FAILED,
        IMAGE_GENERATION_FAILED,
        IMAGE_FILE_MISSING,
        "DEEPSEEK_FAILED",
        "UNEXPECTED_GENERATION_ERROR",
        "SEND_TEXT_FAILED",
        "SEND_IMAGE_FAILED",
        "API_TIMEOUT_PRE_SUBMIT",
        "API_429",
        "API_5XX",
    }
)

_MANUAL_ERROR_TYPES = frozenset(
    {
        "PROMPT_RESULT_UNKNOWN",
        "SEND_RESULT_UNKNOWN",
        "RUN_STATE_CORRUPT",
        "SCHEDULER_STATE_CORRUPT",
        "MESSAGE_SNAPSHOT_INVALID",
        "MISSED_SEND_WINDOW",
        "GROUP_TARGET_MISMATCH",
    }
)


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_unknown_external_result(data: Mapping[str, object]) -> bool:
    image_job = data.get("image_job")
    return bool(
        data.get("prompt_hold")
        or data.get("send_hold_reason") == "SEND_RESULT_UNKNOWN"
        or data.get("send_state") == "unknown"
        or (
            isinstance(image_job, Mapping)
            and image_job.get("status") == "result_unknown"
        )
    )


def failure_is_manual(data: Mapping[str, object]) -> bool:
    error_type = str(data.get("error_type") or data.get("send_error_type") or "")
    error = str(data.get("error") or data.get("send_error") or "")
    return bool(
        error_type in _MANUAL_ERROR_TYPES
        or _is_unknown_external_result(data)
        or "未绑定微信群" in error
    )


def failure_is_retryable(data: Mapping[str, object]) -> bool:
    if failure_is_manual(data):
        return False
    error_type = str(data.get("error_type") or data.get("send_error_type") or "")
    return error_type in _RETRYABLE_ERROR_TYPES


def retry_delay_seconds(attempt_count: int) -> int:
    """有限指数退避：1m、5m、30m，之后保持 30m。"""
    schedule = (60, 300, 1800)
    index = min(max(int(attempt_count), 1), len(schedule)) - 1
    return schedule[index]


def enrich_run_state(
    data: Mapping[str, object],
    previous: Mapping[str, object] | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    """为即将保存的 run 快照补齐可靠性字段。

    同一 failure_fingerprint 的后续状态补写不会重复消耗 retry budget。
    """
    result = dict(data)
    previous = dict(previous or {})
    now = now or datetime.now().astimezone()
    status = str(result.get("status") or PENDING)
    result.setdefault("reliability_schema_version", 1)
    result.setdefault("retry_budget", _positive_int(previous.get("retry_budget"), DEFAULT_RETRY_BUDGET))
    result.setdefault("retry_attempt_count", _positive_int(previous.get("retry_attempt_count"), 0) if previous.get("retry_attempt_count") else 0)
    result["stage"] = STAGE_BY_STATUS.get(status, str(result.get("stage") or "UNKNOWN"))

    checkpoint = CHECKPOINT_BY_STATUS.get(status)
    if checkpoint:
        result["last_successful_checkpoint"] = checkpoint

    if status == SENT:
        result.update(
            execution_state=EXECUTION_COMPLETE,
            retryable=False,
            next_retry_at="",
        )
        return result
    if status == CORRUPT:
        result.update(
            execution_state=EXECUTION_HOLD_MANUAL,
            retryable=False,
            manual_hold=True,
            next_retry_at="",
        )
        return result
    if status != FAILED:
        result.update(
            execution_state=EXECUTION_ACTIVE,
            retryable=False,
            manual_hold=False,
            next_retry_at="",
        )
        return result

    result.setdefault(
        "last_successful_checkpoint",
        str(previous.get("last_successful_checkpoint") or "TASK_CREATED"),
    )
    error_type = str(result.get("error_type") or result.get("send_error_type") or "")
    failed_stage = str(result.get("failed_stage") or "unknown")
    error_summary = str(result.get("error") or result.get("send_error") or "")[:300]
    fingerprint = f"{failed_stage}|{error_type}|{error_summary}"
    previous_fingerprint = str(previous.get("failure_fingerprint") or "")
    attempts = int(previous.get("retry_attempt_count") or 0)
    if fingerprint != previous_fingerprint:
        attempts += 1
    budget = _positive_int(result.get("retry_budget"), DEFAULT_RETRY_BUDGET)
    result.update(
        failure_fingerprint=fingerprint,
        last_error_type=error_type,
        last_error_summary=error_summary,
        last_failure_at=(
            str(previous.get("last_failure_at") or "")
            if fingerprint == previous_fingerprint
            else now.isoformat()
        ),
        retry_attempt_count=attempts,
        retry_budget=budget,
    )
    ledger = list(previous.get("attempt_ledger") or [])
    if fingerprint != previous_fingerprint:
        ledger.append(
            {
                "attempt": attempts,
                "stage": failed_stage,
                "error_type": error_type,
                "error_summary": error_summary,
                "failed_at": now.isoformat(),
            }
        )
    result["attempt_ledger"] = ledger[-20:]

    if failure_is_manual(result):
        result.update(
            execution_state=EXECUTION_HOLD_MANUAL,
            retryable=False,
            manual_hold=True,
            next_retry_at="",
        )
    elif failure_is_retryable(result) and attempts < budget:
        result.update(
            execution_state=EXECUTION_WAIT_RETRY,
            retryable=True,
            manual_hold=False,
            next_retry_at=(now + timedelta(seconds=retry_delay_seconds(attempts))).isoformat(),
        )
    else:
        result.update(
            execution_state=EXECUTION_FAILED_FINAL,
            retryable=False,
            manual_hold=False,
            next_retry_at="",
        )
    return result


def retry_is_due(run: Mapping[str, object], now: datetime | None = None) -> bool:
    if run.get("execution_state") != EXECUTION_WAIT_RETRY:
        return False
    now = now or datetime.now().astimezone()
    raw = str(run.get("next_retry_at") or "")
    if not raw:
        return True
    try:
        retry_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if retry_at.tzinfo is None and now.tzinfo is not None:
        retry_at = retry_at.replace(tzinfo=now.tzinfo)
    if retry_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=retry_at.tzinfo)
    return now >= retry_at
