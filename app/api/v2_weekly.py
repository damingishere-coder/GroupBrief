"""只读周报归档 API。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.config.settings import Settings, get_settings
from app.weekly.store import WeeklyStore

router = APIRouter(prefix="/weekly", tags=["v2-weekly"])


def _store(settings: Settings) -> WeeklyStore:
    return WeeklyStore(settings.output_dir)


def _public_state(state: dict) -> dict:
    return {
        key: value
        for key, value in state.items()
        if key not in {"send_claim_id"}
    }


def _next_weekly_at(settings: Settings, clock: str, now: datetime) -> str:
    try:
        hour, minute = (int(part) for part in clock.split(":", 1))
    except (TypeError, ValueError):
        return ""
    days = (7 - now.weekday()) % 7
    candidate = (now + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate.isoformat()


@router.get("")
def list_weekly_insights(settings: Settings = Depends(get_settings)):
    states = [_public_state(item) for item in _store(settings).list_states()]
    now = datetime.now(ZoneInfo(settings.app_timezone))
    try:
        from app.scheduler.manager import get_scheduler

        scheduler = get_scheduler()
        job_ids = {job.id for job in scheduler.get_jobs()} if scheduler else set()
    except Exception:
        job_ids = set()
    counts: dict[str, int] = {}
    for item in states:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": 2,
        "feature": {
            "generation_enabled": settings.weekly_insights_enabled,
            "send_enabled": settings.weekly_send_enabled,
            "replaces_monday_daily_send": settings.weekly_monday_replacement_enabled,
            "next_generate_at": _next_weekly_at(settings, settings.weekly_generate_time, now),
            "next_send_at": _next_weekly_at(settings, settings.weekly_send_time, now),
            "generation_job_registered": "weekly_insights_generate" in job_ids,
            "send_job_registered": (
                "daily_wechat_send_batch" in job_ids
                if settings.weekly_monday_replacement_enabled
                else "weekly_insights_send" in job_ids
            ),
            "status_counts": counts,
        },
        "items": states,
    }


@router.get("/{week_start}/{group_id}")
def weekly_insight_detail(
    week_start: str,
    group_id: int,
    settings: Settings = Depends(get_settings),
):
    store = _store(settings)
    matches = [
        item
        for item in store.list_states()
        if item.get("week_start") == week_start and int(item.get("group_id") or 0) == group_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="周报不存在")
    state = _public_state(matches[0])
    state["card_url"] = f"/api/v2/weekly/{week_start}/{group_id}/card"
    return state


@router.get("/{week_start}/{group_id}/card")
def weekly_insight_card(
    week_start: str,
    group_id: int,
    settings: Settings = Depends(get_settings),
):
    store = _store(settings)
    matches = [
        item
        for item in store.list_states()
        if item.get("week_start") == week_start and int(item.get("group_id") or 0) == group_id
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="周报不存在")
    path = store.card_path(week_start, str(matches[0].get("week_end") or ""), group_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="周报卡片不存在")
    return FileResponse(path, media_type="image/png", filename="weekly_card.png")
