"""调度与命令行共用的业务终态和退出码契约。"""

from __future__ import annotations

from enum import IntEnum
from typing import Iterable, Mapping


class ProcessExitCode(IntEnum):
    SUCCESS = 0
    FAILED = 1
    PARTIAL = 2
    BLOCKED = 3
    ALREADY_RUNNING = 4
    NOT_RUN = 5


_SUCCESS = frozenset(
    {
        "success",
        "sent",
        "ready_to_send",
        "already_completed",
        "skipped",
        "skipped_disabled",
        "skipped_by_request",
    }
)
_FAILED = frozenset({"failed", "error"})
_PARTIAL = frozenset({"partial"})
_BLOCKED = frozenset(
    {
        "blocked",
        "held",
        "unknown",
        "result_unknown",
        "retry_scheduled",
        "failed_final",
    }
)
_ALREADY_RUNNING = frozenset({"already_running"})
_NOT_RUN = frozenset({"not_run", "no_groups", "no_work", "not_due", "empty"})

_EXIT_CODES = {
    "success": ProcessExitCode.SUCCESS,
    "failed": ProcessExitCode.FAILED,
    "partial": ProcessExitCode.PARTIAL,
    "blocked": ProcessExitCode.BLOCKED,
    "already_running": ProcessExitCode.ALREADY_RUNNING,
    "not_run": ProcessExitCode.NOT_RUN,
}


class SchedulerOutcomeError(RuntimeError):
    """业务终态不是可信成功，必须让 APScheduler 记录本次执行失败。"""

    def __init__(self, outcome: Mapping[str, object]):
        self.outcome = dict(outcome)
        super().__init__(
            "scheduler outcome="
            f"{self.outcome.get('outcome_status', 'failed')} "
            f"exit_code={self.outcome.get('exit_code', int(ProcessExitCode.FAILED))}"
        )


def normalize_status(value: object) -> str:
    """把业务状态收敛成六种稳定终态；未知值一律 fail closed。"""
    status = str(value or "").strip().lower()
    if status in _SUCCESS:
        return "success"
    if status in _FAILED:
        return "failed"
    if status in _PARTIAL:
        return "partial"
    if status in _BLOCKED:
        return "blocked"
    if status in _ALREADY_RUNNING:
        return "already_running"
    if status in _NOT_RUN:
        return "not_run"
    return "failed"


def outcome_for_status(value: object) -> dict:
    outcome_status = normalize_status(value)
    return {
        "outcome_status": outcome_status,
        "exit_code": int(_EXIT_CODES[outcome_status]),
    }


def attach_outcome(payload: Mapping[str, object]) -> dict:
    result = dict(payload)
    result.update(outcome_for_status(result.get("status")))
    return result


def summarize_results(results: Iterable[Mapping[str, object]]) -> dict:
    rows = [dict(item) for item in results]
    if not rows:
        summary = outcome_for_status("not_run")
        summary.update({"result_count": 0, "source_statuses": []})
        return summary

    normalized = [normalize_status(item.get("status")) for item in rows]
    kinds = set(normalized)
    if "blocked" in kinds:
        outcome_status = "blocked"
    elif "already_running" in kinds:
        outcome_status = "already_running" if kinds <= {"already_running", "not_run"} else "partial"
    elif "partial" in kinds:
        outcome_status = "partial"
    elif "failed" in kinds:
        outcome_status = "failed" if kinds == {"failed"} else "partial"
    elif kinds == {"not_run"}:
        outcome_status = "not_run"
    elif "not_run" in kinds:
        outcome_status = "partial"
    else:
        outcome_status = "success"

    summary = outcome_for_status(outcome_status)
    summary.update(
        {
            "result_count": len(rows),
            "source_statuses": sorted({str(item.get("status") or "") for item in rows}),
        }
    )
    return summary


def require_scheduler_success(
    outcome: Mapping[str, object],
    *,
    allow_not_run: bool = False,
) -> None:
    exit_code = int(outcome.get("exit_code", ProcessExitCode.FAILED))
    if exit_code == ProcessExitCode.SUCCESS:
        return
    if allow_not_run and exit_code == ProcessExitCode.NOT_RUN:
        return
    raise SchedulerOutcomeError(outcome)
