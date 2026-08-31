"""进程启动时执行一次的最近 48 小时欠账恢复。

生成按日期从旧到新补跑。发送只处理现有 run.json 明确处于 READY 且未被
hold 的任务，并继续复用 DeliveryStages 的 claim/unknown/目标预检合同。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.daily_v2_job import (
    DailyScheduleState,
    ensure_daily_manifest,
    run_daily_v2_job,
)
from app.scheduler.runtime_status import write_daily_status
from app.v2.run_store import RunStore

logger = get_logger("groupbrief.scheduler")


def _generate_time(value: str) -> time:
    try:
        return datetime.strptime(str(value or ""), "%H:%M").time()
    except (TypeError, ValueError):
        return time(0, 15)


def recovery_dates(now: datetime, lookback_days: int) -> list[str]:
    # 产品安全边界：部署配置即使遗留 30，也不得重新触发整月 AI/生图。
    days = min(max(int(lookback_days), 1), 2)
    start = now.date() - timedelta(days=days - 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(days)]


def run_reliability_watchdog(
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    settings = settings or get_settings()
    tz = ZoneInfo(settings.app_timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    if not settings.reliability_watchdog_enabled:
        return {"status": "disabled", "generation": [], "send": []}

    dates = recovery_dates(now, settings.reliability_lookback_days)
    state_store = DailyScheduleState(settings.output_dir)
    generation_results: list[dict] = []
    generate_time = _generate_time(settings.schedule_generate_time)
    for run_date in dates:
        if run_date == now.date().isoformat() and now.time() < generate_time:
            continue
        state = state_store.load(run_date)
        if state.get("state_status") == "corrupt":
            generation_results.append(
                {
                    "run_date": run_date,
                    "status": "held",
                    "error_type": "SCHEDULER_STATE_CORRUPT",
                }
            )
            continue
        try:
            state = ensure_daily_manifest(
                settings,
                run_date,
                state_store=state_store,
                state=state,
            )
            write_daily_status(RunStore(settings.output_dir), run_date)
        except Exception as exc:
            logger.exception("启动恢复任务清单投影失败：run_date=%s", run_date)
            generation_results.append(
                {
                    "run_date": run_date,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "detail": str(exc)[:300],
                }
            )
            continue
        if state.get("generation_completed_at"):
            continue
        try:
            result = run_daily_v2_job(
                run_date,
                settings=settings,
                skip_email=True,
            )
        except Exception as exc:
            logger.exception("启动恢复生成补偿异常：run_date=%s", run_date)
            result = {
                "run_date": run_date,
                "status": "failed",
                "error_type": type(exc).__name__,
                "detail": str(exc)[:300],
            }
        generation_results.append(result)

    try:
        # 历史任务只能生成恢复；微信自动发送仍只扫描当天。
        send_results = DailyPipeline(settings=settings).send_due_for_dates(
            [now.date().isoformat()], now=now, recovery=False
        )
    except Exception as exc:
        logger.exception("启动恢复发送检查异常")
        send_results = [
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "detail": str(exc)[:300],
            }
        ]

    status = "success"
    if any(item.get("status") in {"failed", "partial"} for item in generation_results + send_results):
        status = "partial"
    if any(item.get("status") in {"held", "blocked"} for item in generation_results + send_results):
        status = "attention_required" if status == "success" else status
    result = {
        "status": status,
        "checked_at": now.isoformat(),
        "dates": dates,
        "generation": generation_results,
        "send": send_results,
    }
    logger.info(
        "启动恢复检查完成：status=%s dates=%d generation=%d send=%d",
        status,
        len(dates),
        len(generation_results),
        len(send_results),
    )
    return result
