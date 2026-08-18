"""群聊管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import Group

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupCreate(BaseModel):
    display_name: str
    wechat_group_id: str = ""
    wechat_group_name: str = ""
    enabled: bool = True
    provider_preference: str = ""
    # V2 扩展
    schedule_rule: str = "weekday_default"
    send_time: str = "08:30"
    summary_model: str = "deepseek-v4-flash"
    prompt_model: str = "deepseek-v4-flash"
    image_enabled: bool = True
    send_target: str = ""
    ranking_template: str = "default"
    image_prompt_template: str = "default"


class GroupUpdate(BaseModel):
    display_name: str | None = None
    wechat_group_id: str | None = None
    wechat_group_name: str | None = None
    enabled: bool | None = None
    provider_preference: str | None = None
    # V2 扩展
    schedule_rule: str | None = None
    send_time: str | None = None
    summary_model: str | None = None
    prompt_model: str | None = None
    image_enabled: bool | None = None
    send_target: str | None = None
    ranking_template: str | None = None
    image_prompt_template: str | None = None


class FromNameRequest(BaseModel):
    name: str
    group_id: str | None = None


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
            "schedule_rule": g.schedule_rule,
            "send_time": g.send_time,
            "summary_model": g.summary_model,
            "prompt_model": g.prompt_model,
            "image_enabled": g.image_enabled,
            "send_target": g.send_target,
            "ranking_template": g.ranking_template,
            "image_prompt_template": g.image_prompt_template,
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


@router.get("/resolve")
def resolve_groups(name: str = Query("")):
    """按群名搜索真实导出数据。空名称返回 400。"""
    if not name.strip():
        raise HTTPException(400, "参数 name 不能为空")
    from app.services.history_service import HistoryService

    service = HistoryService()
    matches = service.resolve_group_names(name.strip())
    return [m.to_dict() for m in matches]


@router.post("/from-name")
def bind_group_from_name(
    payload: FromNameRequest, session: Session = Depends(repo.get_session)
):
    """按群名解析并绑定真实群。单精确匹配自动绑定；歧义需显式 group_id。"""
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "群名称不能为空")

    from app.services.history_service import HistoryService

    service = HistoryService()
    candidates = service.resolve_group_names(name)
    if not candidates:
        raise HTTPException(404, "未找到匹配的群")

    exact_matches = [c for c in candidates if c.match_type == "exact"]

    selected = None
    if len(exact_matches) == 1:
        selected = exact_matches[0]
    elif payload.group_id:
        selected = next(
            (c for c in candidates if c.group_id == payload.group_id),
            None,
        )

    if selected is None:
        raise HTTPException(
            409,
            detail={
                "message": "群名称匹配到多个候选，请携带 group_id 指定其中一个",
                "candidates": [m.to_dict() for m in candidates],
            },
        )

    existing = session.exec(
        select(Group).where(Group.wechat_group_id == selected.group_id)
    ).first()
    if existing:
        return {"id": existing.id, "bound": True, "already_existed": True}

    group = Group(
        display_name=selected.group_name or name,
        wechat_group_id=selected.group_id,
        wechat_group_name=selected.group_name,
    )
    group = repo.save_group(session, group)
    return {"id": group.id, "bound": True, "already_existed": False}


@router.post("/{group_id}/test-read")
def test_read(
    group_id: int,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    from app.scheduler.calendar_rules import get_report_window
    from app.services.history_service import HistoryService

    group = repo.get_group(session, group_id)
    if not group:
        raise HTTPException(404, "群不存在")
    if not group.wechat_group_id:
        raise HTTPException(400, "该群未绑定微信群 ID，请先填写 wechat_group_id")

    window = get_report_window(timezone=settings.app_timezone)
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
