"""群聊管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config.settings import Settings, get_settings
from app.ai.image_themes import DEFAULT_IMAGE_THEME, ImageThemeError, resolve_image_theme, validate_image_theme_config
from app.ai.prompt_builder import _strip_html_comments
from app.ai.prompt_editing import prompt_revision, resolved_theme_text
from app.ai.prompt_templates import (
    ImagePromptTemplateError,
    ImagePromptTemplateService,
    render_image_prompt_template,
    validate_image_prompt_template,
)
from app.db import repository as repo
from app.db.models import Group
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.services.group_name_sync import (
    GroupNameSyncService,
    effective_send_target,
    send_target_mode,
)

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
    summary_model: str = "gpt-5.6-sol"
    prompt_model: str = "gpt-5.6-sol"
    image_enabled: bool = True
    send_target: str = ""
    ranking_template: str = "default"
    image_prompt_template: str = "default"
    image_theme: str = "random_preset"
    image_theme_custom: str = ""
    image_prompt_override: str = ""
    wechat_send_enabled: bool = False


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
    image_theme: str | None = None
    image_theme_custom: str | None = None
    image_prompt_override: str | None = None
    wechat_send_enabled: bool | None = None


class FromNameRequest(BaseModel):
    name: str
    group_id: str | None = None


class GroupImagePromptUpdate(BaseModel):
    content: str = ""
    inherit_global: bool = False
    image_theme: str
    image_theme_custom: str = ""
    expected_revision: str = ""


def _validate_group_theme(theme: object, custom: object = "") -> tuple[str, str]:
    """将主题配置统一交给后端目录校验，并转换成明确的 422。"""
    try:
        return validate_image_theme_config(theme, custom)
    except ImageThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validate_prompt_override(content: object) -> str:
    if content is None:
        return ""
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="群级 Prompt 模板必须是文本")
    if len(content) > 50_000:
        raise HTTPException(status_code=422, detail="群级 Prompt 模板不能超过 50000 字")
    if content.strip():
        try:
            validate_image_prompt_template(content)
        except ImagePromptTemplateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return content


def _group_prompt_payload(group: Group) -> dict:
    templates = ImagePromptTemplateService()
    override = group.image_prompt_override or ""
    source = "group_override" if override.strip() else "global"
    content = override if source == "group_override" else templates.read(group.image_prompt_template or "default")
    theme = resolve_image_theme(
        group.image_theme or DEFAULT_IMAGE_THEME,
        group.image_theme_custom or "",
        group_key=str(group.id or group.wechat_group_id or group.display_name),
    )
    preview = render_image_prompt_template(
        _strip_html_comments(content),
        {
            "group_name": group.display_name or group.wechat_group_name,
            "period_start": "（生成时写入统计开始时间）",
            "period_end": "（生成时写入统计结束时间）",
            "message_count": "（生成时写入消息数）",
            "speaker_count": "（生成时写入发言人数）",
            "image_theme": resolved_theme_text(theme),
        },
    )
    return {
        "group_id": group.id,
        "template_name": group.image_prompt_template or "default",
        "source": source,
        "content": content,
        "revision": prompt_revision(content),
        "image_theme": group.image_theme or DEFAULT_IMAGE_THEME,
        "image_theme_custom": group.image_theme_custom or "",
        "resolved_theme": theme.to_meta(),
        "preview": preview,
    }


def _require_active_group(session: Session, group_id: int) -> Group:
    group = repo.get_active_group(session, group_id)
    if group is None:
        raise HTTPException(404, "群不存在或已移入回收站")
    return group


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
            "effective_send_target": effective_send_target(g),
            "send_target_mode": send_target_mode(g),
            "ranking_template": g.ranking_template,
            "image_prompt_template": g.image_prompt_template,
            "image_theme": g.image_theme,
            "image_theme_custom": g.image_theme_custom,
            "has_image_prompt_override": bool((g.image_prompt_override or "").strip()),
            "wechat_send_enabled": bool(g.wechat_send_enabled),
            "created_at": g.created_at.isoformat(),
            "updated_at": g.updated_at.isoformat(),
        }
        for g in groups
    ]


@router.post("")
def create_group(payload: GroupCreate, session: Session = Depends(repo.get_session)):
    values = payload.model_dump()
    values["send_target"] = str(values.get("send_target") or "").strip()
    values["image_theme"], values["image_theme_custom"] = _validate_group_theme(
        values.get("image_theme", "random_preset"), values.get("image_theme_custom", "")
    )
    values["image_prompt_override"] = _validate_prompt_override(values.get("image_prompt_override", ""))
    wechat_group_id = str(values.get("wechat_group_id") or "").strip()
    existing = repo.find_group_by_wechat_id(session, wechat_group_id) if wechat_group_id else None
    if existing is not None:
        if existing.deleted_at is None:
            raise HTTPException(status_code=409, detail="该微信群已存在于群聊任务")
        if not existing.display_name and values.get("display_name"):
            existing.display_name = values["display_name"]
        if values.get("wechat_group_name"):
            existing.wechat_group_name = values["wechat_group_name"]
        restored = repo.restore_group(session, existing.id)
        return {"id": restored.id, "restored": True, "enabled": False}
    group = Group(**values)
    group = repo.save_group(session, group)
    return {"id": group.id, "restored": False}


@router.put("/{group_id}")
def update_group(
    group_id: int, payload: GroupUpdate, session: Session = Depends(repo.get_session)
):
    group = _require_active_group(session, group_id)
    updates = payload.model_dump(exclude_unset=True)
    if "image_theme" in updates or "image_theme_custom" in updates:
        theme, custom = _validate_group_theme(
            updates.get("image_theme", group.image_theme),
            updates.get("image_theme_custom", group.image_theme_custom),
        )
        updates["image_theme"] = theme
        updates["image_theme_custom"] = custom
    if "image_prompt_override" in updates:
        updates["image_prompt_override"] = _validate_prompt_override(updates["image_prompt_override"])
    if "send_target" in updates:
        updates["send_target"] = str(updates.get("send_target") or "").strip()
    for field, value in updates.items():
        setattr(group, field, value)
    group = repo.save_group(session, group)
    return {"id": group.id}


@router.get("/{group_id}/image-prompt")
def get_group_image_prompt(group_id: int, session: Session = Depends(repo.get_session)):
    group = _require_active_group(session, group_id)
    try:
        return _group_prompt_payload(group)
    except (ImagePromptTemplateError, ImageThemeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{group_id}/image-prompt")
def update_group_image_prompt(
    group_id: int,
    payload: GroupImagePromptUpdate,
    session: Session = Depends(repo.get_session),
):
    group = _require_active_group(session, group_id)
    current = _group_prompt_payload(group)
    if payload.expected_revision and payload.expected_revision != current["revision"]:
        raise HTTPException(status_code=409, detail="群级 Prompt 已被其他页面修改，请刷新后重试")
    theme, custom = _validate_group_theme(payload.image_theme, payload.image_theme_custom)
    if not payload.inherit_global and not payload.content.strip():
        raise HTTPException(status_code=422, detail="群级 Prompt 不能为空；如需继承请使用恢复全局模板")
    group.image_theme = theme
    group.image_theme_custom = custom
    group.image_prompt_override = "" if payload.inherit_global else _validate_prompt_override(payload.content)
    repo.save_group(session, group)
    return _group_prompt_payload(group)


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


@router.post("/sync-wechat-names")
def sync_wechat_names(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    """按稳定微信群 ID 刷新当前名称；不打开微信，也不发送任何内容。"""
    source = WeChatDataAnalysisSource(settings=settings)
    report = GroupNameSyncService(source).sync(session)
    return report.to_dict()


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
        if existing.deleted_at is not None:
            existing.wechat_group_name = selected.group_name or existing.wechat_group_name
            if not existing.display_name:
                existing.display_name = selected.group_name or name
            restored = repo.restore_group(session, existing.id)
            return {
                "id": restored.id,
                "bound": True,
                "already_existed": True,
                "restored": True,
                "enabled": False,
            }
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
    from app.services.message_normalizer import MessageNormalizer

    group = _require_active_group(session, group_id)
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
        "message_count": sum(
            1 for message in outcome.messages if MessageNormalizer.is_countable(message)
        ),
        "raw_message_count": len(outcome.messages),
    }


@router.post("/{group_id}/verify-send-target")
def verify_send_target(
    group_id: int,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    """只查找并核验微信目标，不发送任何文字或图片。"""
    group = _require_active_group(session, group_id)
    target = effective_send_target(group)
    if not target:
        raise HTTPException(422, "该群没有可验证的发送目标")

    from app.sender.wechat_native import create_wechat_sender

    sender = create_wechat_sender(settings=settings)
    verify = getattr(sender, "verify_target", None)
    if not callable(verify):
        raise HTTPException(status_code=409, detail=f"当前发送器 {sender.name} 不支持无副作用目标核验")
    ok, detail = verify(target)
    if not ok:
        raise HTTPException(status_code=409, detail=detail)
    return {"ok": True, "target": target, "detail": detail}


@router.delete("/{group_id}")
def delete_group(group_id: int, session: Session = Depends(repo.get_session)):
    group = repo.delete_group(session, group_id)
    if group is None:
        raise HTTPException(404, "群不存在")
    return {"ok": True, "deleted_at": group.deleted_at.isoformat() if group.deleted_at else None}


@router.post("/{group_id}/restore")
def restore_group(group_id: int, session: Session = Depends(repo.get_session)):
    group = repo.restore_group(session, group_id)
    if group is None:
        raise HTTPException(404, "群不存在")
    return {
        "ok": True,
        "id": group.id,
        "enabled": bool(group.enabled),
        "wechat_send_enabled": bool(group.wechat_send_enabled),
    }
