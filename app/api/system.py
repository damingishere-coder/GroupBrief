"""系统状态 API。"""

from __future__ import annotations

from datetime import datetime
import os
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlmodel import Session, select

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import ProviderHealth as StoredProviderHealth
from app.scheduler.calendar_rules import get_report_window

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def readiness(
    request: Request,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    """只检查本地关键依赖；不调用外部 Provider，也不写文件或数据库。"""
    checks: dict[str, dict] = {}
    try:
        session.exec(text("SELECT 1")).first()
        checks["database"] = {
            "ok": True,
            "status": "OK",
            "detail": "数据库连接可用",
        }
    except Exception as exc:
        checks["database"] = {
            "ok": False,
            "status": "UNAVAILABLE",
            "detail": str(exc)[:200],
        }

    output_ok = settings.output_dir.is_dir() and os.access(
        settings.output_dir,
        os.W_OK,
    )
    checks["output"] = {
        "ok": output_ok,
        "status": "OK" if output_ok else "UNAVAILABLE",
        "detail": "output 目录存在且可写" if output_ok else "output 目录不存在或不可写",
    }

    try:
        from app.ai.prompt_templates import ImagePromptTemplateService
        from app.ranking.template_service import RankingTemplateService

        templates_ok = (
            "default" in RankingTemplateService().list_templates()
            and "default" in ImagePromptTemplateService().list_templates()
        )
        template_detail = "默认排行和生图模板可读" if templates_ok else "缺少默认模板"
    except Exception as exc:
        templates_ok = False
        template_detail = str(exc)[:200]
    checks["templates"] = {
        "ok": templates_ok,
        "status": "OK" if templates_ok else "UNAVAILABLE",
        "detail": template_detail,
    }

    startup_error = str(
        getattr(request.app.state, "startup_check_error", "") or ""
    )
    checks["startup_capture"] = {
        "ok": not startup_error,
        "status": "OK" if not startup_error else "ERROR",
        "detail": startup_error or "启动检查结果已保留",
    }
    ready = all(item["ok"] for item in checks.values())
    payload = {
        "ready": ready,
        "checks": checks,
        "scheduler_owner": getattr(
            request.app.state,
            "scheduler_owner",
            settings.scheduler_owner,
        ),
        "scheduler_active": bool(
            getattr(request.app.state, "scheduler_active", False)
        ),
    }
    return JSONResponse(payload, status_code=200 if ready else 503)


def _provider_health_payload(row: StoredProviderHealth) -> dict:
    return {
        "status": row.status,
        "detail": row.detail,
        "ok": row.status == "OK",
        "checked_at": row.checked_at.isoformat() if row.checked_at else "",
    }


def _prune_provider_health(
    session: Session,
    *,
    max_per_provider: int = 100,
) -> int:
    rows = session.exec(
        select(StoredProviderHealth).order_by(
            StoredProviderHealth.provider,
            StoredProviderHealth.checked_at.desc(),
            StoredProviderHealth.id.desc(),
        )
    ).all()
    counts: dict[str, int] = {}
    removed = 0
    for row in rows:
        counts[row.provider] = counts.get(row.provider, 0) + 1
        if counts[row.provider] <= max_per_provider:
            continue
        session.delete(row)
        removed += 1
    return removed


@router.get("/providers")
def providers(
    session: Session = Depends(repo.get_session),
):
    """读取最近一次已保存的 Provider 健康结果，不执行外部检查。"""
    rows = session.exec(
        select(StoredProviderHealth).order_by(
            StoredProviderHealth.checked_at.desc(),
            StoredProviderHealth.id.desc(),
        )
    ).all()
    latest: dict[str, StoredProviderHealth] = {}
    for row in rows:
        latest.setdefault(row.provider, row)
    return {name: _provider_health_payload(row) for name, row in latest.items()}


@router.post("/providers/refresh")
def refresh_providers(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    """显式执行 Provider 深度检查，保存结果并限制历史记录数量。"""
    from app.providers.history.registry import check_all_health

    health = check_all_health(settings)
    now = datetime.now()
    for name, h in health.items():
        session.add(
            StoredProviderHealth(
                provider=name,
                status=h.status.value,
                detail=h.detail[:500],
                checked_at=now,
            )
        )
    session.flush()
    _prune_provider_health(session)
    session.commit()
    return {
        name: {
            "status": h.status.value,
            "detail": h.detail,
            "ok": h.ok,
            "checked_at": now.isoformat(),
        }
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
