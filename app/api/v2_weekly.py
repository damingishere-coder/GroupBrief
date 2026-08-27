"""只读周报归档 API。"""

from __future__ import annotations

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


@router.get("")
def list_weekly_insights(settings: Settings = Depends(get_settings)):
    states = [_public_state(item) for item in _store(settings).list_states()]
    return {"schema_version": 1, "items": states}


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
