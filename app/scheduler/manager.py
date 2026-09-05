"""APScheduler 调度管理：每日定点生成、定点串行发送与按需一次性恢复。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from zoneinfo import ZoneInfo

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.scheduler.daily_v2_job import DailyScheduleState, run_daily_v2_job
from app.scheduler.heartbeat import record_scheduler_heartbeat
from app.scheduler.outcome import require_scheduler_success, summarize_results
from app.scheduler.reliability_watchdog import recovery_dates, run_reliability_watchdog
from app.scheduler.send_job import run_send_due_job
from app.v2.constants import EXECUTION_WAIT_RETRY, IMAGE_READY, READY_TO_SEND
from app.v2.run_store import RunStore
from app.weekly.service import WeeklyInsightsService, previous_natural_week
from app.weekly.store import WeeklyStore

logger = get_logger("groupbrief.scheduler")

_scheduler: BackgroundScheduler | None = None
_DEFAULT_GENERATE_TIME = time(0, 15)
_DEFAULT_SEND_TIME = time(8, 30)


def _normalize_now(settings: Settings, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.app_timezone)
    value = now or datetime.now(tz)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _parse_clock(value: str, *, fallback: time, field_name: str) -> time:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%H:%M").time()
        if parsed.strftime("%H:%M") != text:
            raise ValueError("时间必须使用 HH:MM 格式")
        return parsed
    except (TypeError, ValueError):
        logger.warning(
            "无效的 %s=%r，已回退到 %s",
            field_name,
            value,
            fallback.strftime("%H:%M"),
        )
        return fallback


def _parse_generate_time(value: str) -> time:
    return _parse_clock(
        value,
        fallback=_DEFAULT_GENERATE_TIME,
        field_name="schedule_generate_time",
    )


def _parse_send_time(value: str) -> time:
    return _parse_clock(
        value,
        fallback=_DEFAULT_SEND_TIME,
        field_name="schedule_send_time",
    )


def _parse_weekly_time(value: str) -> time:
    return _parse_clock(
        value,
        fallback=time(7, 45),
        field_name="weekly_generate_time",
    )


def _is_current_monday_weekly_replacement(
    settings: Settings,
    now: datetime,
    run_date: str,
) -> bool:
    """仅替换本周一当天的日报发送，历史日期仍保持人工恢复边界。"""
    return bool(
        settings.weekly_monday_replacement_enabled
        and now.weekday() == 0
        and run_date == now.date().isoformat()
    )


def _timestamp(value: object, *, now: datetime) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(now.tzinfo)


def _add_one_shot(
    scheduler: BackgroundScheduler,
    *,
    job_id: str,
    name: str,
    func,
    run_at: datetime,
    args: list | None = None,
    kwargs: dict | None = None,
) -> None:
    scheduler.add_job(
        func,
        trigger=DateTrigger(run_date=run_at, timezone=run_at.tzinfo),
        args=args or [],
        kwargs=kwargs or {},
        id=job_id,
        name=name,
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
    )


def _schedule_on_demand_jobs(
    scheduler: BackgroundScheduler | None,
    settings: Settings,
    *,
    now: datetime | None = None,
    run_dates: list[str] | None = None,
    include_newly_ready_send: bool = True,
) -> list[str]:
    """从持久化状态重建有限的一次性任务；不进行任何外部调用。"""
    if scheduler is None:
        return []
    now = _normalize_now(settings, now)
    today = now.date().isoformat()
    allowed_dates = recovery_dates(now, settings.reliability_lookback_days)
    selected_dates = sorted(set(run_dates or allowed_dates) & set(allowed_dates))
    state_store = DailyScheduleState(settings.output_dir)
    run_store = RunStore(settings.output_dir)
    scheduled: list[str] = []

    for run_date in selected_dates:
        state = state_store.load(run_date)
        if state.get("state_status") == "corrupt" or state.get("generation_completed_at"):
            continue
        retry_times: list[datetime] = []
        state_retry = _timestamp(state.get("next_retry_at"), now=now)
        if state_retry is not None:
            retry_times.append(state_retry)
        for run in run_store.list_runs(run_date):
            if str(run.get("execution_state") or "") != EXECUTION_WAIT_RETRY:
                continue
            retry_at = _timestamp(run.get("next_retry_at"), now=now)
            if retry_at is not None:
                retry_times.append(retry_at)
        if not retry_times:
            continue
        run_at = max(min(retry_times), now + timedelta(seconds=1))
        job_id = f"daily_v2_retry_{run_date.replace('-', '')}"
        _add_one_shot(
            scheduler,
            job_id=job_id,
            name=f"DailyV2Retry:{run_date}",
            func=run_scheduled_daily_v2_job,
            run_at=run_at,
            args=[run_date],
            kwargs={"skip_email": True},
        )
        scheduled.append(job_id)

    if today not in selected_dates:
        return scheduled

    if _is_current_monday_weekly_replacement(settings, now, today):
        # 日报仍生成并保留 READY_TO_SEND，但周一微信入口由周报接管。
        return scheduled

    send_clock = _parse_send_time(settings.schedule_send_time)
    due_at = datetime.combine(now.date(), send_clock, tzinfo=now.tzinfo)
    if now < due_at:
        return scheduled
    cutoff = due_at + timedelta(
        minutes=max(int(settings.wechat_late_send_window_minutes), 0)
    )
    ready_runs = [
        run
        for run in run_store.list_runs(today)
        if run.get("status") in {IMAGE_READY, READY_TO_SEND}
        and bool(run.get("wechat_send_enabled"))
        and not run.get("sent_at")
        and not run.get("send_hold")
    ]
    if not ready_runs:
        return scheduled

    retry_times: list[datetime] = []
    has_immediate = False
    for run in ready_runs:
        retry_at = _timestamp(run.get("send_next_retry_at"), now=now)
        if retry_at is None:
            has_immediate = has_immediate or include_newly_ready_send
        elif retry_at <= now:
            has_immediate = True
        else:
            retry_times.append(retry_at)
    if not has_immediate and not retry_times:
        return scheduled
    if now > cutoff or has_immediate:
        run_at = now + timedelta(seconds=1)
    else:
        run_at = min(retry_times)
        if run_at > cutoff:
            run_at = cutoff + timedelta(seconds=1)

    job_id = f"daily_send_once_{today.replace('-', '')}"
    _add_one_shot(
        scheduler,
        job_id=job_id,
        name=f"DailySendOnce:{today}",
        func=run_scheduled_send_batch,
        run_at=run_at,
        args=[today],
    )
    scheduled.append(job_id)
    return scheduled


def run_scheduled_daily_v2_job(
    run_date: str | None = None,
    *,
    skip_email: bool = False,
) -> dict:
    """APScheduler 生成包装器：完成后仅按持久化状态安排一次性后续任务。"""
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
    target_date = run_date or _normalize_now(settings).date().isoformat()
    _schedule_on_demand_jobs(
        _scheduler,
        settings,
        run_dates=[target_date],
    )
    require_scheduler_success(result)
    return result


def run_scheduled_send_batch(run_date: str | None = None) -> dict:
    """08:30 核心批次和按需补偿共用的串行发送入口。"""
    settings = get_settings()
    now = _normalize_now(settings)
    target_date = run_date or now.date().isoformat()
    try:
        if _is_current_monday_weekly_replacement(settings, now, target_date):
            return run_scheduled_weekly_send(settings=settings, now=now)
        return run_send_due_job(
            settings=settings,
            now=now,
            run_date=target_date,
        )
    finally:
        _schedule_on_demand_jobs(
            _scheduler,
            settings,
            now=now,
            run_dates=[target_date],
            include_newly_ready_send=False,
        )


def run_scheduled_startup_recovery() -> dict:
    """进程启动后只执行一次恢复检查，并重建尚未执行的一次性任务。"""
    settings = get_settings()
    now = _normalize_now(settings)
    record_scheduler_heartbeat(settings, job="startup_recovery", status="started")
    try:
        result = run_reliability_watchdog(settings=settings, now=now)
    except Exception as exc:
        record_scheduler_heartbeat(
            settings,
            job="startup_recovery",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    _schedule_on_demand_jobs(
        _scheduler,
        settings,
        now=now,
        run_dates=recovery_dates(now, settings.reliability_lookback_days),
        include_newly_ready_send=False,
    )
    _schedule_weekly_replacement_jobs(_scheduler, settings, now=now)
    record_scheduler_heartbeat(
        settings,
        job="startup_recovery",
        status=str(result.get("status") or "unknown"),
    )
    return result


def run_scheduled_weekly_insights(
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    """周一独立生成上一自然周归档；不读取原始聊天、不发送。"""
    settings = settings or get_settings()
    supplied_now = now
    now = _normalize_now(settings, now)
    record_scheduler_heartbeat(settings, job="weekly_insights", status="started")
    try:
        result = WeeklyInsightsService(settings).generate_previous_week(now=now)
    except Exception as exc:
        record_scheduler_heartbeat(
            settings,
            job="weekly_insights",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_scheduler_heartbeat(
        settings,
        job="weekly_insights",
        status=str(result.get("status") or "unknown"),
    )
    _schedule_weekly_replacement_jobs(
        _scheduler,
        settings,
        now=(now if supplied_now is not None else _normalize_now(settings)),
        include_generation=False,
    )
    return result


def run_scheduled_weekly_send(
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict:
    """可选周报发送独立运行，不再参与日报批次或空闲日志。"""
    settings = settings or get_settings()
    now = _normalize_now(settings, now)
    results = WeeklyInsightsService(settings).send_due(now=now)
    outcome = summarize_results(results)
    require_scheduler_success(outcome, allow_not_run=True)
    return outcome


def _schedule_weekly_replacement_jobs(
    scheduler: BackgroundScheduler | None,
    settings: Settings,
    *,
    now: datetime | None = None,
    include_generation: bool = True,
) -> list[str]:
    """周一重启或延迟生成后，按周报持久化状态恢复一次性任务。"""
    if scheduler is None:
        return []
    now = _normalize_now(settings, now)
    today = now.date().isoformat()
    if not _is_current_monday_weekly_replacement(settings, now, today):
        return []

    scheduled: list[str] = []
    generate_due = datetime.combine(
        now.date(),
        _parse_weekly_time(settings.weekly_generate_time),
        tzinfo=now.tzinfo,
    )
    if include_generation and now >= generate_due:
        job_id = f"weekly_generate_once_{today.replace('-', '')}"
        _add_one_shot(
            scheduler,
            job_id=job_id,
            name=f"WeeklyGenerateOnce:{today}",
            func=run_scheduled_weekly_insights,
            run_at=now + timedelta(seconds=1),
        )
        scheduled.append(job_id)

    send_due = datetime.combine(
        now.date(),
        _parse_clock(
            settings.weekly_send_time,
            fallback=_DEFAULT_SEND_TIME,
            field_name="weekly_send_time",
        ),
        tzinfo=now.tzinfo,
    )
    if now < send_due:
        return scheduled
    week_start, week_end = previous_natural_week(now.date())
    sendable = any(
        state.get("week_start") == week_start.isoformat()
        and state.get("week_end") == week_end.isoformat()
        and state.get("status") in {"ready_to_send", "sending"}
        for state in WeeklyStore(settings.output_dir).list_states()
    )
    if sendable:
        job_id = f"weekly_send_once_{today.replace('-', '')}"
        _add_one_shot(
            scheduler,
            job_id=job_id,
            name=f"WeeklySendOnce:{today}",
            func=run_scheduled_weekly_send,
            run_at=now + timedelta(seconds=1),
        )
        scheduled.append(job_id)
    return scheduled


def _schedule_startup_recovery(
    scheduler: BackgroundScheduler,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    if not settings.reliability_watchdog_enabled:
        return False
    now = _normalize_now(settings, now)
    _add_one_shot(
        scheduler,
        job_id="startup_recovery",
        name="StartupRecovery",
        func=run_scheduled_startup_recovery,
        run_at=now + timedelta(seconds=5),
    )
    return True


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(settings.app_timezone)
    generate_time = _parse_generate_time(settings.schedule_generate_time)
    send_time = _parse_send_time(settings.schedule_send_time)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_scheduled_daily_v2_job,
        trigger=CronTrigger(
            hour=generate_time.hour,
            minute=generate_time.minute,
            second=0,
            timezone=tz,
        ),
        id="daily_v2_generate_email",
        name="DailyV2GenerateAndEmail",
        misfire_grace_time=1800,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_scheduled_send_batch,
        trigger=CronTrigger(
            hour=send_time.hour,
            minute=send_time.minute,
            second=0,
            timezone=tz,
        ),
        id="daily_wechat_send_batch",
        name="DailyWechatSendBatch",
        misfire_grace_time=max(int(settings.wechat_late_send_window_minutes) * 60, 60),
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
                second=0,
                timezone=tz,
            ),
            id="weekly_insights_generate",
            name="WeeklyInsightsGenerate",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
        )
    if settings.weekly_send_enabled and not settings.weekly_monday_replacement_enabled:
        weekly_send_time = _parse_clock(
            settings.weekly_send_time,
            fallback=_DEFAULT_SEND_TIME,
            field_name="weekly_send_time",
        )
        scheduler.add_job(
            run_scheduled_weekly_send,
            trigger=CronTrigger(
                day_of_week="mon",
                hour=weekly_send_time.hour,
                minute=weekly_send_time.minute,
                second=0,
                timezone=tz,
            ),
            id="weekly_insights_send",
            name="WeeklyInsightsSend",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
        )
    scheduler.start()
    _scheduler = scheduler
    record_scheduler_heartbeat(settings, job="scheduler", status="started")
    _schedule_startup_recovery(scheduler, settings)
    _schedule_weekly_replacement_jobs(scheduler, settings)
    logger.info(
        "调度已启动：每日 %s 生成，%s 微信串行发送批次（时区 %s）",
        generate_time.strftime("%H:%M"),
        send_time.strftime("%H:%M"),
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


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("调度已停止")


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
