"""V2 生图主题、Prompt 编辑与运行级图片命令。"""

from __future__ import annotations

from datetime import datetime
import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.api.v2_ui_common import (
    ImageThemeResolveBody,
    RunPromptUpdateBody,
    make_store as _store,
    safe_group_dir as _safe_group_dir,
    validate_api_run_date as _validate_run_date,
)
from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.v2.run_store import RunStore


router = APIRouter()


@router.get("/image-themes")
def image_themes():
    from app.ai.image_themes import public_image_theme_options

    return {"themes": public_image_theme_options()}


@router.post("/image-themes/resolve")
def resolve_theme_preview(
    body: ImageThemeResolveBody,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    from app.ai.image_themes import ImageThemeError, resolve_image_theme
    from app.ai.prompt_editing import replace_theme_section, resolved_theme_text

    try:
        if body.run_date:
            _validate_run_date(body.run_date)
        group_key = str(body.group_id or "preview")
        group_name = group_key
        if isinstance(body.group_id, int) or (
            isinstance(body.group_id, str) and body.group_id.isdigit()
        ):
            group = repo.get_active_group(session, int(body.group_id))
            if group is not None:
                group_name = (
                    group.display_name or group.wechat_group_name or group_key
                )
        previous_signature = (
            _store(settings).previous_theme_signature(group_name, body.run_date)
            if body.run_date and body.group_id is not None
            else ""
        )
        theme = resolve_image_theme(
            body.image_theme,
            body.image_theme_custom,
            group_key=group_key,
            run_date=body.run_date,
            previous_signature=previous_signature,
        )
    except ImageThemeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "requested_key": theme.requested_key,
        "actual_key": theme.actual_key,
        "display_name": theme.display_name,
        "theme_text": resolved_theme_text(theme),
        "style_signature": theme.style_signature,
        "style_seed": theme.style_seed,
        "prompt": replace_theme_section(body.prompt, theme) if body.prompt else "",
    }


def _read_run_prompt(
    store: RunStore,
    group: str,
    run_date: str,
) -> tuple[str, dict]:
    _safe_group_dir(store, group, run_date)
    if not store.run_path(group, run_date).exists():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    path = store.prompt_path(group, run_date)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image_prompt.txt 不存在")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Prompt 读取失败：{exc}") from exc
    return content, store.load_run(group, run_date)


@router.get("/runs/{group}/{run_date}/prompt")
def get_run_prompt(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    from app.ai.prompt_editing import prompt_revision

    _validate_run_date(run_date)
    store = _store(settings)
    content, run = _read_run_prompt(store, group, run_date)
    prompt_meta = (
        run.get("prompt_meta") if isinstance(run.get("prompt_meta"), dict) else {}
    )
    return {
        "group_name": group,
        "run_date": run_date,
        "content": content,
        "revision": prompt_revision(content),
        "has_original": store.original_prompt_path(group, run_date).is_file(),
        "image_theme": (
            prompt_meta.get("requested_theme")
            or run.get("image_theme")
            or "random_preset"
        ),
        "image_theme_custom": run.get("image_theme_custom") or "",
        "prompt_edited_at": run.get("prompt_edited_at") or "",
        "topic_selection": prompt_meta.get("topic_selection"),
    }


@router.put("/runs/{group}/{run_date}/prompt")
def update_run_prompt(
    group: str,
    run_date: str,
    body: RunPromptUpdateBody,
    settings: Settings = Depends(get_settings),
):
    from app.ai.image_themes import ImageThemeError, resolve_image_theme
    from app.ai.prompt_editing import (
        prompt_revision,
        replace_theme_section,
        validate_prompt_text,
    )

    _validate_run_date(run_date)
    store = _store(settings)
    current, current_run = _read_run_prompt(store, group, run_date)
    if body.expected_revision != prompt_revision(current):
        raise HTTPException(
            status_code=409,
            detail="Prompt 已被其他页面修改，请刷新后重试",
        )
    try:
        current_meta = (
            current_run.get("prompt_meta")
            if isinstance(current_run.get("prompt_meta"), dict)
            else None
        )
        theme = resolve_image_theme(
            body.image_theme,
            body.image_theme_custom,
            group_key=str(current_run.get("group_id") or group),
            run_date=run_date,
            persisted_meta=current_meta,
        )
        content = validate_prompt_text(
            replace_theme_section(body.content, theme)
        )
    except (ImageThemeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    prompt_path = store.prompt_path(group, run_date)
    original_path = store.original_prompt_path(group, run_date)
    if not original_path.exists():
        shutil.copy2(prompt_path, original_path)
    temp = prompt_path.with_suffix(".txt.tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(prompt_path)
    meta = theme.to_meta()
    store.update(
        group,
        run_date,
        image_theme=body.image_theme,
        image_theme_custom=body.image_theme_custom.strip(),
        prompt_meta={
            **(store.load_run(group, run_date).get("prompt_meta") or {}),
            **meta,
            "source": "manual",
        },
        prompt_edited_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        image_regen_status="prompt_saved",
        image_regen_error="",
        send_hold=True,
        needs_manual_send=True,
    )
    return get_run_prompt(group, run_date, settings)


@router.post("/runs/{group}/{run_date}/prompt/restore")
def restore_run_prompt(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    _validate_run_date(run_date)
    store = _store(settings)
    _safe_group_dir(store, group, run_date)
    if not store.run_path(group, run_date).exists():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    original = store.original_prompt_path(group, run_date)
    if not original.is_file():
        raise HTTPException(status_code=404, detail="没有可恢复的原始 Prompt")
    shutil.copy2(original, store.prompt_path(group, run_date))
    store.update(
        group,
        run_date,
        prompt_edited_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        image_regen_status="prompt_restored",
        send_hold=True,
        needs_manual_send=True,
    )
    return get_run_prompt(group, run_date, settings)


@router.post("/runs/{group}/{run_date}/regenerate-image", status_code=202)
def regenerate_run_image(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    from app.image.regeneration import enqueue_regeneration

    _validate_run_date(run_date)
    _safe_group_dir(_store(settings), group, run_date)
    try:
        run = enqueue_regeneration(settings, group, run_date)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"accepted": True, "run": run}


def _run_group_id(store: RunStore, group: str, run_date: str) -> int:
    run = store.load_run(group, run_date)
    try:
        return int(run.get("group_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="运行记录缺少可用群 ID") from exc


@router.post("/runs/{group}/{run_date}/refresh-messages")
def refresh_run_messages(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    """更新当天消息和确定性排行榜；不会重建 Prompt、生图或发送。"""
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    _validate_run_date(run_date)
    store = _store(settings)
    _safe_group_dir(store, group, run_date)
    if not store.run_path(group, run_date).exists():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    try:
        result = DailyPipeline(settings=settings, dry_run=False).force_generate(
            _run_group_id(store, group, run_date),
            run_date,
            refresh_messages=True,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                result.get("detail")
                or result.get("error")
                or "消息刷新失败"
            ),
        )
    return {"result": result, "run": store.load_run(group, run_date)}


@router.post("/runs/{group}/{run_date}/rebuild-prompt")
def rebuild_run_prompt(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    """只从已保存的 messages.json 重建排行榜和 Prompt；不会取数或生图。"""
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    _validate_run_date(run_date)
    store = _store(settings)
    _safe_group_dir(store, group, run_date)
    if not store.run_path(group, run_date).exists():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    try:
        result = DailyPipeline(
            settings=settings,
            dry_run=False,
        ).rebuild_prompt_from_snapshot(
            _run_group_id(store, group, run_date),
            run_date,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                result.get("detail")
                or result.get("error")
                or "Prompt 重建失败"
            ),
        )
    return {"result": result, "run": store.load_run(group, run_date)}
