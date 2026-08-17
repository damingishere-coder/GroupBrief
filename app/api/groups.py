"""群聊管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.models import Group

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupCreate(BaseModel):
    display_name: str
    wechat_group_id: str = ""
    wechat_group_name: str = ""
    enabled: bool = True
    provider_preference: str = ""


class GroupUpdate(BaseModel):
    display_name: str | None = None
    wechat_group_id: str | None = None
    wechat_group_name: str | None = None
    enabled: bool | None = None
    provider_preference: str | None = None


@router.get("")
def list_groups(session: Session = Depends(repo.get_session)):
    groups = repo.list_groups(session)
    return [
        {
            "id": g.id,
            "display_name": g.display_name,
            "wechat_group_id": g.wechat_group_id,
            "wechat_group_name": g.wechat_group_name,
            "enabled": g.enabled,
            "provider_preference": g.provider_preference,
            "created_at": g.created_at.isoformat(),
            "updated_at": g.updated_at.isoformat(),
        }
        for g in groups
    ]


@router.post("")
def create_group(payload: GroupCreate, session: Session = Depends(repo.get_session)):
    group = Group(**payload.model_dump())
    group = repo.save_group(session, group)
    return {"id": group.id}


@router.put("/{group_id}")
def update_group(
    group_id: int, payload: GroupUpdate, session: Session = Depends(repo.get_session)
):
    group = repo.get_group(session, group_id)
    if not group:
        raise HTTPException(404, "群不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    group = repo.save_group(session, group)
    return {"id": group.id}


@router.get("/discover")
def discover_groups():
    from app.services.history_service import HistoryService

    service = HistoryService()
    groups = service.discover_groups()
    return [
        {
            "group_id": g.group_id,
            "group_name": g.group_name,
            "member_count": g.member_count,
        }
        for g in groups
    ]


@router.post("/{group_id}/test-read")
def test_read(group_id: int, session: Session = Depends(repo.get_session)):
    from datetime import timedelta

    from app.scheduler.calendar_rules import get_report_window
    from app.services.history_service import HistoryService

    group = repo.get_group(session, group_id)
    if not group:
        raise HTTPException(404, "群不存在")
    if not group.wechat_group_id:
        raise HTTPException(400, "该群未绑定微信群 ID，请先填写 wechat_group_id")

    window = get_report_window(timezone="Asia/Shanghai")
    service = HistoryService()
    outcome = service.fetch(
        group.wechat_group_id,
        group.wechat_group_name or group.display_name,
        window.range_start,
        window.range_end,
    )
    return {
        "provider": outcome.provider,
        "status": outcome.status.value,
        "detail": outcome.detail,
        "message_count": len(outcome.messages),
    }


@router.delete("/{group_id}")
def delete_group(group_id: int, session: Session = Depends(repo.get_session)):
    repo.delete_group(session, group_id)
    return {"ok": True}
