"""V2 管理 API（P8 前端依赖）。

Dashboard / 历史日报 / 系统健康 / Pipeline 手动操作 / 输出文件读取。
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.scheduler.period import PeriodResolver
from app.v2.constants import FILE_IMAGE
from app.v2.run_store import RunStore, validate_run_date

router = APIRouter(prefix="/api/v2", tags=["v2-ui"])

# 允许前端直接读取的输出文件
ALLOWED_FILES = frozenset(
    {
        "messages.json",
        "ranking.json",
        "ranking.txt",
        "image_prompt.txt",
        "image_prompt.original.txt",
        "daily_image.png",
        "daily_image.previous.png",
        "run.json",
    }
)


class PipelineGenerateBody(BaseModel):
    group_id: int | None = None
    run_date: str | None = None
    force: bool = False
    refresh_messages: bool = False


class PipelineSendBody(BaseModel):
    group_id: int
    run_date: str | None = None
    confirm_regenerated: bool = False
    confirm_late_send: bool = False


class ImageThemeResolveBody(BaseModel):
    image_theme: str
    image_theme_custom: str = ""
    prompt: str = ""
    group_id: int | str | None = None
    run_date: str = ""


class RunPromptUpdateBody(BaseModel):
    content: str
    expected_revision: str
    image_theme: str
    image_theme_custom: str = ""


def _tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _store(settings: Settings) -> RunStore:
    return RunStore(settings.output_dir)


# ---------- Dashboard ----------


@router.get("/dashboard")
def dashboard(session: Session = Depends(repo.get_session), settings: Settings = Depends(get_settings)):
    tz = _tz(settings)
    now = datetime.now(tz)
    today = now.date().isoformat()

    resolver = PeriodResolver()
    window = resolver.resolve(run_date=now.date(), timezone=settings.app_timezone)
    store = _store(settings)
    groups = repo.list_groups(session, only_enabled=True)

    cards: list[dict] = []
    counts = {"pending": 0, "generated": 0, "sent": 0, "failed": 0}
    for g in groups:
        name = g.display_name or g.wechat_group_name
        run = store.load_run(name, today)
        status = run.get("status", "PENDING")
        image_path = store.image_path(name, today)
        image_url = ""
        if image_path.exists() and Path(image_path).stat().st_size > 0:
            from urllib.parse import quote

            image_url = f"/api/v2/files/{quote(name)}/{today}/{FILE_IMAGE}"
        cards.append(
            {
                "group_id": g.id,
                "group_name": name,
                "send_time": g.send_time,
                "schedule_rule": g.schedule_rule,
                "image_enabled": bool(g.image_enabled),
                "wechat_send_enabled": bool(getattr(g, "wechat_send_enabled", False)),
                "ranking_template": g.ranking_template,
                "image_prompt_template": g.image_prompt_template,
                "status": status,
                "period_start": run.get("period_start", ""),
                "period_end": run.get("period_end", ""),
                "message_count": run.get("message_count", 0),
                "speaker_count": run.get("speaker_count", 0),
                "image_url": image_url,
                "error": run.get("error") or run.get("image_error") or run.get("error_type") or "",
                "sent_at": run.get("sent_at", ""),
                "send_hold": bool(run.get("send_hold")),
                "updated_at": run.get("updated_at", ""),
            }
        )
        if status in ("SENT",):
            counts["sent"] += 1
        elif status in ("IMAGE_READY", "READY_TO_SEND"):
            counts["generated"] += 1
        elif status == "FAILED":
            counts["failed"] += 1
        else:
            counts["pending"] += 1

    # 下一次发送时间（最早到点且未发送的群）
    next_send = ""
    upcoming = []
    for card in cards:
        if card["status"] not in ("IMAGE_READY", "READY_TO_SEND"):
            continue
        if card["sent_at"]:
            continue
        if not card["wechat_send_enabled"] or card["send_hold"]:
            continue
        try:
            h, m = card["send_time"].split(":")
            send_at = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        except Exception:
            send_at = now.replace(hour=8, minute=30, second=0, microsecond=0)
        upcoming.append((send_at, card["group_name"]))
    if upcoming:
        earliest, name = min(upcoming, key=lambda x: x[0])
        next_send = f"{earliest.strftime('%H:%M')}（{name}）"

    return {
        "today": today,
        "should_run": window.should_run,
        "period_start": window.period_start_str(),
        "period_end": window.period_end_str(),
        "enabled_groups": len(cards),
        "counts": counts,
        "next_send": next_send,
        "cards": cards,
    }


# ---------- 历史日报 ----------


@router.get("/runs")
def list_runs(settings: Settings = Depends(get_settings), run_date: str | None = None):
    if run_date is not None:
        _validate_run_date(run_date)
    runs = _store(settings).list_runs(run_date)
    return {"runs": runs, "total": len(runs)}


_ARCHIVE_RUN_FIELDS = (
    "group_id",
    "wechat_group_id",
    "group_name",
    "run_date",
    "report_date",
    "status",
    "period_start",
    "period_end",
    "message_count",
    "speaker_count",
    "sent_at",
    "error",
    "error_type",
    "image_error",
    "updated_at",
)


def _archive_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _archive_local_id(value: object) -> int | None:
    text = _archive_text(value)
    return int(text) if text.isdigit() else None


def _archive_run_summary(run: dict) -> dict | None:
    if not isinstance(run, dict):
        return None
    run_date = _archive_text(run.get("run_date"))
    try:
        validate_run_date(run_date)
    except ValueError:
        return None
    return {field: run.get(field, "") for field in _ARCHIVE_RUN_FIELDS}


def _archive_group_entry(group) -> dict:
    deleted_at = getattr(group, "deleted_at", None)
    return {
        "archive_key": f"group:{group.id}",
        "group_id": group.id,
        "wechat_group_id": group.wechat_group_id or "",
        "display_name": group.display_name or group.wechat_group_name or f"群 {group.id}",
        "state": "deleted" if deleted_at is not None else "active",
        "enabled": bool(group.enabled) if deleted_at is None else False,
        "deleted_at": deleted_at.isoformat() if deleted_at is not None else None,
        "created_at": group.created_at.isoformat() if group.created_at else "",
        "runs": [],
    }


def _match_archive_group(run: dict, groups_by_id: dict, groups_by_wechat: dict):
    raw_group_id = _archive_text(run.get("group_id"))
    local_id = _archive_local_id(raw_group_id)
    wechat_id = _archive_text(run.get("wechat_group_id"))
    if not wechat_id and raw_group_id and local_id is None:
        wechat_id = raw_group_id

    if local_id is not None and local_id in groups_by_id:
        candidate = groups_by_id[local_id]
        candidate_wechat = _archive_text(candidate.wechat_group_id)
        if wechat_id and candidate_wechat and wechat_id != candidate_wechat:
            return None
        return candidate

    if wechat_id:
        candidates = groups_by_wechat.get(wechat_id, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return None

    return None


def _orphan_archive_key(run: dict) -> str:
    raw_group_id = _archive_text(run.get("group_id"))
    wechat_id = _archive_text(run.get("wechat_group_id"))
    if not wechat_id and raw_group_id and _archive_local_id(raw_group_id) is None:
        wechat_id = raw_group_id
    if wechat_id:
        return f"orphan:wechat:{wechat_id}"
    if raw_group_id:
        return f"orphan:local:{raw_group_id}"
    return f"orphan:name:{_archive_text(run.get('group_name')) or 'unknown'}"


@router.get("/archive/groups")
def archive_groups(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    """按稳定群身份聚合 V2 归档；不返回任何本机绝对路径。"""
    db_groups = repo.list_groups(session, include_deleted=True)
    entries = {group.id: _archive_group_entry(group) for group in db_groups if group.id is not None}
    groups_by_id = {group.id: group for group in db_groups if group.id is not None}
    groups_by_wechat: dict[str, list] = {}

    for group in db_groups:
        wechat_id = _archive_text(group.wechat_group_id)
        if wechat_id:
            groups_by_wechat.setdefault(wechat_id, []).append(group)

    orphan_entries: dict[str, dict] = {}
    for raw_run in _store(settings).list_runs():
        run = _archive_run_summary(raw_run)
        if run is None:
            continue
        group = _match_archive_group(raw_run, groups_by_id, groups_by_wechat)
        if group is not None and group.id in entries:
            entries[group.id]["runs"].append(run)
            continue

        key = _orphan_archive_key(raw_run)
        orphan = orphan_entries.setdefault(
            key,
            {
                "archive_key": key,
                "group_id": None,
                "wechat_group_id": _archive_text(raw_run.get("wechat_group_id")),
                "display_name": _archive_text(raw_run.get("group_name")) or "历史遗留群",
                "state": "orphaned",
                "enabled": False,
                "deleted_at": None,
                "created_at": "",
                "runs": [],
            },
        )
        orphan["runs"].append(run)

    for entry in [*entries.values(), *orphan_entries.values()]:
        entry["runs"].sort(
            key=lambda item: (_archive_text(item.get("run_date")), _archive_text(item.get("updated_at"))),
            reverse=True,
        )
        entry["run_count"] = len(entry["runs"])
        entry["run_dates"] = sorted(
            {_archive_text(item.get("run_date")) for item in entry["runs"] if item.get("run_date")},
            reverse=True,
        )

    active = sorted(
        (entry for entry in entries.values() if entry["state"] == "active"),
        key=lambda entry: entry["group_id"],
    )
    deleted = sorted(
        (entry for entry in entries.values() if entry["state"] == "deleted"),
        key=lambda entry: entry.get("deleted_at") or "",
        reverse=True,
    )
    orphaned = sorted(
        orphan_entries.values(),
        key=lambda entry: entry["runs"][0].get("run_date", "") if entry["runs"] else "",
        reverse=True,
    )
    return {
        "groups": [*active, *deleted, *orphaned],
        "active_count": len(active),
        "trash_count": len(deleted) + len(orphaned),
    }


@router.get("/runs/{group}/{run_date}")
def run_detail(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    _validate_run_date(run_date)
    store = _store(settings)
    run = store.load_run(group, run_date)
    group_dir = store.group_dir(group, run_date)
    files = (
        sorted(p.name for p in group_dir.glob("*") if p.is_file() and p.name in ALLOWED_FILES)
        if group_dir.exists()
        else []
    )
    return {"run": run, "files": files}


# ---------- 生图主题与 Prompt 编辑 ----------


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
        if isinstance(body.group_id, int) or (isinstance(body.group_id, str) and body.group_id.isdigit()):
            group = repo.get_active_group(session, int(body.group_id))
            if group is not None:
                group_name = group.display_name or group.wechat_group_name or group_key
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
    return {
        "requested_key": theme.requested_key,
        "actual_key": theme.actual_key,
        "display_name": theme.display_name,
        "theme_text": resolved_theme_text(theme),
        "style_signature": theme.style_signature,
        "style_seed": theme.style_seed,
        "prompt": replace_theme_section(body.prompt, theme) if body.prompt else "",
    }


def _read_run_prompt(store: RunStore, group: str, run_date: str) -> tuple[str, dict]:
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
def get_run_prompt(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    from app.ai.prompt_editing import prompt_revision

    _validate_run_date(run_date)
    store = _store(settings)
    content, run = _read_run_prompt(store, group, run_date)
    prompt_meta = run.get("prompt_meta") if isinstance(run.get("prompt_meta"), dict) else {}
    return {
        "group_name": group,
        "run_date": run_date,
        "content": content,
        "revision": prompt_revision(content),
        "has_original": store.original_prompt_path(group, run_date).is_file(),
        # random_preset 在 Prompt 构建时已经解析；运行级编辑必须继续使用当次固定主题。
        "image_theme": prompt_meta.get("requested_theme") or run.get("image_theme") or "random_preset",
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
    from app.ai.prompt_editing import prompt_revision, replace_theme_section, validate_prompt_text

    _validate_run_date(run_date)
    store = _store(settings)
    current, current_run = _read_run_prompt(store, group, run_date)
    if body.expected_revision != prompt_revision(current):
        raise HTTPException(status_code=409, detail="Prompt 已被其他页面修改，请刷新后重试")
    try:
        current_meta = current_run.get("prompt_meta") if isinstance(current_run.get("prompt_meta"), dict) else None
        theme = resolve_image_theme(
            body.image_theme,
            body.image_theme_custom,
            group_key=str(current_run.get("group_id") or group),
            run_date=run_date,
            persisted_meta=current_meta,
        )
        content = validate_prompt_text(replace_theme_section(body.content, theme))
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
        prompt_meta={**(store.load_run(group, run_date).get("prompt_meta") or {}), **meta, "source": "manual"},
        prompt_edited_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        image_regen_status="prompt_saved",
        image_regen_error="",
        send_hold=True,
        needs_manual_send=True,
    )
    return get_run_prompt(group, run_date, settings)


@router.post("/runs/{group}/{run_date}/prompt/restore")
def restore_run_prompt(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    _validate_run_date(run_date)
    store = _store(settings)
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
def regenerate_run_image(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    from app.image.regeneration import enqueue_regeneration

    _validate_run_date(run_date)
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
def refresh_run_messages(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    """更新当天消息和确定性排行榜；不会重建 Prompt、生图或发送。"""
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    _validate_run_date(run_date)
    store = _store(settings)
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
        raise HTTPException(status_code=409, detail=result.get("detail") or result.get("error") or "消息刷新失败")
    return {"result": result, "run": store.load_run(group, run_date)}


@router.post("/runs/{group}/{run_date}/rebuild-prompt")
def rebuild_run_prompt(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    """只从已保存的 messages.json 重建排行榜和 Prompt；不会取数或生图。"""
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    _validate_run_date(run_date)
    store = _store(settings)
    if not store.run_path(group, run_date).exists():
        raise HTTPException(status_code=404, detail="运行记录不存在")
    try:
        result = DailyPipeline(settings=settings, dry_run=False).rebuild_prompt_from_snapshot(
            _run_group_id(store, group, run_date),
            run_date,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.get("status") == "failed":
        raise HTTPException(status_code=409, detail=result.get("detail") or result.get("error") or "Prompt 重建失败")
    return {"result": result, "run": store.load_run(group, run_date)}


# ---------- 系统健康 ----------


@router.get("/system/health")
def system_health(settings: Settings = Depends(get_settings)):
    checks: dict[str, dict] = {}

    from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource

    source = WeChatDataAnalysisSource(settings=settings)
    h = source.health_check()
    checks["wechat_data_analysis"] = {"ok": h.ok, "status": h.status.value, "detail": h.detail}

    from app.providers.ai.codex import CodexGPTProvider

    codex_summary = CodexGPTProvider(settings)
    codex_summary_report = codex_summary.health_report()
    codex_summary_ok, codex_summary_detail = codex_summary.health_check()
    checks["codex_summary"] = {
        "ok": codex_summary_ok,
        "status": "OK" if codex_summary_ok else "UNAVAILABLE",
        "detail": codex_summary_detail,
        "model": codex_summary_report["model"],
        "binary": codex_summary_report["binary"],
        "version": codex_summary_report["version"],
    }

    checks["deepseek_fallback"] = {
        "ok": bool(settings.ai_api_key),
        "status": "OK" if settings.ai_api_key else "UNAVAILABLE",
        "detail": f"备用模型 {settings.ai_model}，API Key {'已配置' if settings.ai_api_key else '未配置'}",
    }

    from app.image.codex_generator import CodexImageGenerator

    codex = CodexImageGenerator(settings=settings)
    codex_report = codex.health_report()
    codex_ok, codex_detail = codex.health_check()
    checks["codex_imagegen"] = {
        "ok": codex_ok,
        "status": "OK" if codex_ok else "UNAVAILABLE",
        "detail": codex_detail,
        "binary": codex_report["binary"],
        "version": codex_report["version"],
        "last_image_smoke": codex_report["last_image_smoke"],
    }

    from app.sender.wechat_native import create_wechat_sender

    sender = create_wechat_sender(settings=settings)
    send_ok, send_detail = sender.health_check()
    sender_report = getattr(sender, "health_report", lambda: {"ok": send_ok})()
    checks["wechat_sender"] = {
        "ok": send_ok,
        "status": "OK" if send_ok else "UNAVAILABLE",
        "detail": send_detail,
        "dependencies": sender_report.get("dependencies", {"ok": send_ok, "detail": send_detail}),
        "desktop": sender_report.get("desktop", {"ok": send_ok, "detail": send_detail}),
        "ocr": sender_report.get("ocr", {"ok": send_ok, "detail": send_detail}),
        "clipboard": sender_report.get("clipboard", {"ok": send_ok, "detail": send_detail}),
        "window": sender_report.get("window", {"ok": send_ok, "detail": send_detail}),
    }

    # output 可写
    try:
        settings.ensure_dirs()
        test = settings.output_dir / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        checks["output"] = {"ok": True, "status": "OK", "detail": "output 目录可写"}
    except Exception as e:
        checks["output"] = {"ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]}

    # 模板完整性
    from app.ranking.template_service import RankingTemplateService

    try:
        ranking_names = RankingTemplateService().list_templates()
        checks["templates"] = {"ok": True, "status": "OK", "detail": f"排行榜模板 {ranking_names}"}
    except Exception as e:
        checks["templates"] = {"ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]}

    # 最近一次完整任务 + 未完成任务
    store = _store(settings)
    runs = store.list_runs()
    last_run = runs[0] if runs else {}
    incomplete = sum(1 for r in runs if r.get("status") not in ("SENT", "FAILED"))
    checks["recent_task"] = {
        "ok": True,
        "status": "OK",
        "detail": (
            f"最近：{last_run.get('group_name', '—')} {last_run.get('run_date', '—')} "
            f"{last_run.get('status', '—')}；未完成任务 {incomplete} 个"
        ),
    }

    return {"checks": checks, "warnings": _environment_warnings()}


# ---------- 启动检查 / 恢复（P9） ----------


@router.get("/system/startup")
def startup_checks(settings: Settings = Depends(get_settings)):
    from app.core.startup_check import run_startup_checks

    return {"checks": run_startup_checks(settings)}


@router.get("/system/recovery")
def recovery_info(settings: Settings = Depends(get_settings)):
    from app.v2.recovery import scan_incomplete, verify_output

    store = _store(settings)
    return {
        "incomplete": scan_incomplete(store),
        "integrity": verify_output(store),
    }


class RetryBody(BaseModel):
    group_id: int | None = None
    run_date: str | None = None


@router.post("/pipeline/retry-failed")
def retry_failed(body: RetryBody | None = None, settings: Settings = Depends(get_settings)):
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.v2.recovery import scan_incomplete

    body = body or RetryBody()
    if body.run_date is not None:
        _validate_run_date(body.run_date)
    pipeline = DailyPipeline()
    if body.group_id:
        r = pipeline.force_generate(body.group_id, body.run_date)
        return {"results": [{"group_id": body.group_id, "status": r.get("status"), "detail": r.get("error") or ""}]}
    incomplete = scan_incomplete(_store(settings), body.run_date)
    results: list[dict] = []
    for run in incomplete:
        group_name = run["group_name"]
        from sqlmodel import Session, select

        from app.db import repository as repo
        from app.db.models import Group

        with Session(repo.engine) as session:
            group = session.exec(select(Group).where(Group.display_name == group_name)).first()
        if group is None:
            results.append({"group_name": group_name, "status": "skipped", "detail": "群不存在/已停用"})
            continue
        if run.get("recovery_type") == "send":
            r = pipeline.force_send(group.id, run["run_date"])
        else:
            r = pipeline.force_generate(group.id, run["run_date"])
        results.append({"group_name": group_name, "status": r.get("status"), "detail": r.get("error") or ""})
    return {"results": results}


def _environment_warnings() -> list[str]:
    """P9：休眠/锁屏等无人值守风险提示。"""
    warnings: list[str] = []
    warnings.append(
        "休眠/锁屏风险：GroupBrief 自动发送依赖桌面会话。请保持电脑开机、不休眠、不锁屏；"
        "Windows 电源设置请关闭自动休眠与自动锁屏。"
    )
    return warnings


# ---------- Pipeline 手动操作 ----------


@router.post("/pipeline/generate")
def pipeline_generate(body: PipelineGenerateBody):
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    if body.run_date is not None:
        _validate_run_date(body.run_date)
    pipeline = DailyPipeline(dry_run=False)
    try:
        if body.group_id:
            result = pipeline.force_generate(
                body.group_id,
                body.run_date,
                refresh_messages=body.refresh_messages,
            )
            return {"results": [result]}
        results = pipeline.generate_all(
            run_date=body.run_date,
            force=body.force,
            refresh_messages=body.refresh_messages,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"results": results}


@router.post("/pipeline/send-due")
def pipeline_send_due():
    from app.pipeline.daily_pipeline import DailyPipeline

    pipeline = DailyPipeline(dry_run=False)
    results = pipeline.send_due()
    return {"results": results}


@router.post("/pipeline/send")
def pipeline_send(body: PipelineSendBody):
    from app.pipeline.daily_pipeline import DailyPipeline

    if body.run_date is not None:
        _validate_run_date(body.run_date)
    pipeline = DailyPipeline(dry_run=False)
    result = pipeline.force_send(
        body.group_id,
        body.run_date,
        confirm_regenerated=body.confirm_regenerated,
        confirm_late_send=body.confirm_late_send,
    )
    return {"result": result}


# ---------- 输出文件读取 ----------


@router.get("/files/{group}/{run_date}/{file_name}")
def read_output_file(
    group: str,
    run_date: str,
    file_name: str,
    settings: Settings = Depends(get_settings),
):
    _validate_run_date(run_date)
    if file_name not in ALLOWED_FILES:
        raise HTTPException(400, f"不允许访问的文件：{file_name}")
    path = _store(settings).group_dir(group, run_date) / file_name
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=file_name)


def _validate_run_date(run_date: str) -> str:
    try:
        return validate_run_date(run_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
