"""每日微信串行发送批次。"""

from __future__ import annotations

from datetime import datetime

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.outcome import require_scheduler_success, summarize_results
from app.scheduler.heartbeat import record_scheduler_heartbeat

logger = get_logger("groupbrief.scheduler")


def run_send_due_job(
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
    run_date: str | None = None,
) -> dict:
    """按稳定群 ID 串行处理一天的日报发送，不再夹带周报扫描。"""
    settings = settings or get_settings()
    record_scheduler_heartbeat(settings, job="send_batch", status="started")
    try:
        pipeline = DailyPipeline(settings=settings)
        if run_date is None:
            results = pipeline.send_due() if now is None else pipeline.send_due(now=now)
        else:
            results = pipeline.send_due_for_dates(
                [run_date],
                now=now,
                recovery=False,
            )
    except Exception as exc:
        logger.exception("微信发送批次调度异常")
        record_scheduler_heartbeat(
            settings,
            job="send_batch",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    outcome = summarize_results(results)
    if results:
        logger.info(
            "微信发送批次终态：outcome=%s exit_code=%d result_count=%d source_statuses=%s groups=%s",
            outcome["outcome_status"],
            outcome["exit_code"],
            outcome["result_count"],
            outcome["source_statuses"],
            [
                {
                    key: item.get(key)
                    for key in ("group_name", "status", "error_type", "detail")
                    if item.get(key) not in (None, "")
                }
                for item in results
            ],
        )
    record_scheduler_heartbeat(
        settings,
        job="send_batch",
        status=str(outcome.get("outcome_status") or "unknown"),
    )
    require_scheduler_success(outcome, allow_not_run=True)
    return outcome
