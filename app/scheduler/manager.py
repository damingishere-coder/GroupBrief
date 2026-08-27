"""APScheduler 调度管理：每日唯一 V2 任务 + 分钟级微信发送。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.scheduler.daily_v2_job import DailyScheduleState, run_daily_v2_job
from app.scheduler.outcome import require_scheduler_success
from app.scheduler.send_job import run_send_due_job
from app.scheduler.heartbeat import record_scheduler_heartbeat
from app.scheduler.reliability_watchdog import run_reliability_watchdog
from app.weekly.service import WeeklyInsightsService

logger = get_logger("groupbrief.scheduler")

_scheduler: BackgroundScheduler | None = None
_DEFAULT_GENERATE_TIME = time(0, 15)


def run_scheduled_daily_v2_job(
    run_date: str | None = None,
    *,
    skip_email: bool = False,
) -> dict:
    """APScheduler 包装器：业务非成功时必须让调度器记录异常。"""
    settings = get_settings()
    record_scheduler_heartbeat(settings, job="daily_v2", status="started")
    try:
        result = run_daily_v2_job(run_date, settings=settings, skip_email=skip_email)
    except Exception as exc:
        record_scheduler_heartbeat(
            settings,
            job="daily_v2",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_scheduler_heartbeat(
        settings,
        job="daily_v2",
        status=str(result.get("outcome_status") or result.get("status") or "unknown"),
    )
    logger.info(
        "APScheduler 每日任务结果：status=%s outcome=%s exit_code=%s",
        result.get("status"),
        result.get("outcome_status"),
        result.get("exit_code"),
    )
    require_scheduler_success(result)
    return result


def run_scheduled_reliability_watchdog() -> dict:
    settings = get_settings()
    record_scheduler_heartbeat(settings, job="reliability_watchdog", status="started")
    try:
        result = run_reliability_watchdog(settings=settings)
    except Exception as exc:
        record_scheduler_heartbeat(
            settings,
            job="reliability_watchdog",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_scheduler_heartbeat(
        settings,
        job="reliability_watchdog",
        status=str(result.get("status") or "unknown"),
    )
    return result


def run_scheduled_weekly_insights() -> dict:
    """周一独立生成上一自然周归档；不读取原始聊天、不发送。"""
    settings = get_settings()
    record_scheduler_heartbeat(settings, job="weekly_insights", status="started")
    result = WeeklyInsightsService(settings).generate_previous_week()
    record_scheduler_heartbeat(
        settings,
        job="weekly_insights",
        status=str(result.get("status") or "unknown"),
    )
    return result


def _parse_generate_time(value: str) -> time:
    """解析每日生成时间；无效配置安全回退到 00:15。"""
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
        if parsed.strftime("%H:%M") != text:
            raise ValueError("时间必须使用 HH:MM 格式")
        return parsed
    except (TypeError, ValueError):
        logger.warning("无效的 schedule_generate_time=%r，已回退到 00:15", value)
        return _DEFAULT_GENERATE_TIME


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(settings.app_timezone)
    generate_time = _parse_generate_time(settings.schedule_generate_time)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_scheduled_daily_v2_job,
        trigger=CronTrigger(
            hour=generate_time.hour,
            minute=generate_time.minute,
            timezone=tz,
        ),
        id="daily_v2_generate_email",
        name="DailyV2GenerateAndEmail",
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )
    if settings.weekly_insights_enabled:
        weekly_time = _parse_weekly_time(settings.weekly_generate_time)
        scheduler.add_job(
            run_scheduled_weekly_insights,
            trigger=CronTrigger(
                day_of_week="mon",
                hour=weekly_time.hour,
                minute=weekly_time.minute,
                timezone=tz,
            ),
            id="weekly_insights_generate",
            name="WeeklyInsightsGenerate",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
        )
    scheduler.add_job(
        run_send_due_job,
        trigger=CronTrigger(minute="*", second=15, timezone=tz),
        id="send_wechat_due",
        name="SendWechatDue",
        misfire_grace_time=45,
        coalesce=True,
        max_instances=1,
    )
    if settings.reliability_watchdog_enabled:
        scheduler.add_job(
            run_scheduled_reliability_watchdog,
            trigger=IntervalTrigger(
                minutes=max(int(settings.reliability_watchdog_interval_minutes), 1),
                timezone=tz,
            ),
            id="reliability_watchdog",
            name="ReliabilityWatchdog",
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )
    scheduler.start()
    record_scheduler_heartbeat(settings, job="scheduler", status="started")
    _schedule_startup_catchup(scheduler, settings)
    _schedule_startup_watchdog(scheduler, settings)
    _scheduler = scheduler
    logger.info(
        "调度已启动：%s V2 生成+邮件，微信每分钟第 15 秒 send_due（时区 %s）",
        generate_time.strftime("%H:%M"),
        settings.app_timezone,
    )
    logger.info(
        "生成协调器配置：model=%s group_limit=%d fetch_limit=%d ai_limit=%d direct_chars=%d",
        settings.ai_model,
        settings.generation_group_concurrency,
        settings.wechat_fetch_concurrency,
        settings.ai_request_concurrency,
        settings.max_context_chars,
    )
    return scheduler


def _parse_weekly_time(value: str) -> time:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
        if parsed.strftime("%H:%M") != text:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        logger.warning("无效的 weekly_generate_time=%r，已回退到 07:45", value)
        return time(7, 45)


def _schedule_startup_watchdog(
    scheduler: BackgroundScheduler,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    if not settings.reliability_watchdog_enabled:
        return False
    tz = ZoneInfo(settings.app_timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    scheduler.add_job(
        run_scheduled_reliability_watchdog,
        trigger=DateTrigger(run_date=now + timedelta(seconds=5), timezone=tz),
        id="reliability_watchdog_startup",
        name="ReliabilityWatchdogStartup",
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
    )
    return True


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("调度已停止")


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler


def _schedule_startup_catchup(
    scheduler: BackgroundScheduler,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """每日生成时间后若当天没有完成标记，安排一次启动补偿。"""
    if not settings.schedule_startup_catchup_enabled:
        return False
    tz = ZoneInfo(settings.app_timezone)
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    generate_time = _parse_generate_time(settings.schedule_generate_time)
    if now.time() < generate_time:
        return False
    run_date = now.date().isoformat()
    state = DailyScheduleState(settings.output_dir).load(run_date)
    if state.get("state_status") == "corrupt":
        logger.error("跳过启动补偿：run_date=%s scheduler state corrupt", run_date)
        return False
    if state.get("generation_completed_at"):
        return False
    scheduler.add_job(
        run_scheduled_daily_v2_job,
        trigger=DateTrigger(run_date=now + timedelta(seconds=3), timezone=tz),
        args=[run_date],
        kwargs={"skip_email": True},
        id="daily_v2_startup_catchup",
        name="DailyV2StartupCatchup",
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
    )
    logger.info("已安排 V2 启动补偿：run_date=%s", run_date)
    return True
