"""系统状态 API。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.scheduler.calendar_rules import get_report_window

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/providers")
def providers(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    from datetime import datetime

    from app.db.models import ProviderHealth
    from app.providers.history.registry import check_all_health

    health = check_all_health(settings)
    now = datetime.now()
    for name, h in health.items():
        session.add(
            ProviderHealth(
                provider=name,
                status=h.status.value,
                detail=h.detail[:500],
                checked_at=now,
            )
        )
    session.commit()
    return {
        name: {"status": h.status.value, "detail": h.detail, "ok": h.ok}
        for name, h in health.items()
    }


@router.get("/stats", deprecated=True)
def stats(session: Session = Depends(repo.get_session)):
    """仪表盘统计卡数据：最近一次成功 run 的消息总数 / 发言人数。"""
    from sqlmodel import select

    from app.db.models import GroupRun

    runs = repo.find_runs(session, 10)
    latest = next((r for r in runs if r.status in ("success", "partial")), None)
    if latest is None:
        return {
            "total_messages": 0,
            "total_speakers": 0,
            "last_report_date": "",
            "run_id": None,
        }
    rows = session.exec(
        select(GroupRun).where(
            GroupRun.run_id == latest.id,
            GroupRun.identity_state == "linked",
        )
    ).all()
    return {
        "total_messages": sum(r.message_count for r in rows),
        "total_speakers": sum(r.speaker_count for r in rows),
        "last_report_date": latest.report_date,
        "run_id": latest.id,
    }


@router.get("/status")
def status(session: Session = Depends(repo.get_session), settings: Settings = Depends(get_settings)):
    from app.scheduler.manager import get_scheduler

    try:
        tz = ZoneInfo(settings.app_timezone)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
        tz = None

    window = get_report_window(now.date(), settings.app_timezone)
    next_run = ""
    if window.should_run:
        from datetime import time

        gen_time = settings.schedule_generate_time  # HH:MM
        hour, minute = (int(x) for x in gen_time.split(":"))
        next_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_dt <= now:
            next_dt = next_dt.replace(day=next_dt.day + 1)
        next_run = next_dt.isoformat()

    groups = repo.list_groups(session, only_enabled=True)
    return {
        "version": "1.0.0",
        "status": "running",
        "scheduler_owner": settings.scheduler_owner,
        "scheduler_active": get_scheduler() is not None,
        "legacy_v1_write_mode": settings.legacy_v1_write_mode,
        "legacy_v1_writes_active": settings.legacy_v1_write_mode == "maintenance",
        "now": now.isoformat() if tz else None,
        "timezone": settings.app_timezone,
        "report_date": window.report_date.isoformat(),
        "range_start": window.range_start.isoformat() if window.should_run else "",
        "range_end": window.range_end.isoformat() if window.should_run else "",
        "should_run_today": window.should_run,
        "is_weekend_summary": window.is_weekend_summary,
        "next_generate_at": next_run,
        "enabled_groups": len(groups),
        "total_groups": len(repo.list_groups(session)),
    }
