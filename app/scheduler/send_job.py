"""分钟级微信待发送扫描任务。"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.outcome import require_scheduler_success, summarize_results
from app.scheduler.heartbeat import record_scheduler_heartbeat
from app.weekly.service import WeeklyInsightsService

logger = get_logger("groupbrief.scheduler")


def run_send_due_job() -> dict:
    """只处理已显式启用、到时、未发送且未被人工审核拦截的运行。"""
    settings = get_settings()
    record_scheduler_heartbeat(settings, job="send_due", status="started")
    try:
        # 同一 APScheduler job 内严格串行：先日报，再周报；状态和 claim 完全独立。
        results = DailyPipeline(settings=settings).send_due()
        results.extend(WeeklyInsightsService(settings).send_due())
    except Exception as exc:
        logger.exception("微信 send_due 调度异常")
        record_scheduler_heartbeat(
            settings,
            job="send_due",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    outcome = summarize_results(results)
    logger.info(
        "微信 send_due 终态：outcome=%s exit_code=%d result_count=%d source_statuses=%s groups=%s",
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
        job="send_due",
        status=str(outcome.get("outcome_status") or "unknown"),
    )
    require_scheduler_success(outcome, allow_not_run=True)
    return outcome
