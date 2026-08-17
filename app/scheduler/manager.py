"""APScheduler 调度管理。

- 每天 08:45：GenerateDailyReports（周一~周六；周日由日历规则跳过）
- 每天 09:00：SendDailyEmail
- 时区：Asia/Shanghai
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.core.logging import get_logger
from app.scheduler.email_job import run_email_job
from app.scheduler.generate_job import run_generate_job

logger = get_logger("groupbrief.scheduler")

_scheduler: BackgroundScheduler | None = None


def start_scheduler(settings: Settings) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = ZoneInfo(settings.app_timezone)
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_generate_job,
        trigger=CronTrigger(hour=8, minute=45, timezone=tz),
        id="generate_daily",
        name="GenerateDailyReports",
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        run_email_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=tz),
        id="send_daily_email",
        name="SendDailyEmail",
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "调度已启动：08:45 %s，09:00 %s（时区 %s）",
        "GenerateDailyReports",
        "SendDailyEmail",
        settings.app_timezone,
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
