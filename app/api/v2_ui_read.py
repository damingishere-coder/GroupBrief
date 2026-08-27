"""V2 Dashboard、归档、运行详情与输出文件查询。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.v2_ui_common import (
    ALLOWED_FILES,
    make_store as _store,
    safe_group_dir as _safe_group_dir,
    timezone_for as _tz,
    validate_api_run_date as _validate_run_date,
)
from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.scheduler.period import PeriodResolver
from app.v2.constants import FILE_IMAGE
from app.v2.run_store import validate_run_date


router = APIRouter()


@router.get("/dashboard")
def dashboard(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
    run_date: str | None = None,
):
    tz = _tz(settings)
    now = datetime.now(tz)
    selected_date = now.date()
    if run_date is not None:
        selected_date = date.fromisoformat(_validate_run_date(run_date))
    selected_run_date = selected_date.isoformat()
    window = PeriodResolver().resolve(
        run_date=selected_date,
        timezone=settings.app_timezone,
    )
    store = _store(settings)
    groups = repo.list_groups(session, only_enabled=True)

    cards: list[dict] = []
    counts = {"pending": 0, "generated": 0, "sent": 0, "failed": 0, "held": 0}
    for group in groups:
        name = group.display_name or group.wechat_group_name
        run = store.load_run(name, selected_run_date)
        status = run.get("status", "PENDING")
        image_path = store.image_path(name, selected_run_date)
        image_url = ""
        if image_path.exists() and Path(image_path).stat().st_size > 0:
            image_url = f"/api/v2/files/{quote(name)}/{selected_run_date}/{FILE_IMAGE}"
        ranking_preview: list[dict[str, object]] = []
        ranking_error = ""
        ranking_path = store.ranking_json_path(name, selected_run_date)
        if ranking_path.exists() and ranking_path.stat().st_size > 0:
            try:
                ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
                speakers = ranking.get("top_speakers", []) if isinstance(ranking, dict) else []
                if not isinstance(speakers, list):
                    raise ValueError("top_speakers 不是数组")
                for index, item in enumerate(speakers[:5], start=1):
                    if not isinstance(item, dict):
                        continue
                    speaker_name = str(item.get("name") or "").strip()
                    count = item.get("count")
                    if not speaker_name or not isinstance(count, (int, float)):
                        continue
                    ranking_preview.append(
                        {
                            "rank": int(item.get("rank") or index),
                            "name": speaker_name,
                            "count": int(count),
                        }
                    )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                ranking_error = f"排行榜预览不可用：{exc}"
        cards.append(
            {
                "group_id": group.id,
                "group_name": name,
                "send_time": group.send_time,
                "schedule_rule": group.schedule_rule,
                "image_enabled": bool(group.image_enabled),
                "wechat_send_enabled": bool(
                    getattr(group, "wechat_send_enabled", False)
                ),
                "ranking_template": group.ranking_template,
                "image_prompt_template": group.image_prompt_template,
                "status": status,
                "period_start": run.get("period_start", ""),
                "period_end": run.get("period_end", ""),
                "message_count": run.get("message_count", 0),
                "speaker_count": run.get("speaker_count", 0),
                "image_url": image_url,
                "ranking_preview": ranking_preview,
                "ranking_error": ranking_error,
                "error": (
                    run.get("error")
                    or run.get("image_error")
                    or run.get("send_error")
                    or run.get("error_type")
                    or ""
                ),
                "sent_at": run.get("sent_at", ""),
                "prompt_hold": bool(run.get("prompt_hold")),
                "prompt_hold_reason": str(run.get("prompt_hold_reason") or ""),
                "prompt_operation_id": str(run.get("prompt_operation_id") or ""),
                "prompt_operation_status": str(run.get("prompt_operation_status") or ""),
                "send_hold": bool(run.get("send_hold")),
                "send_state": str(run.get("send_state") or ""),
                "send_hold_reason": str(run.get("send_hold_reason") or ""),
                "send_error": str(run.get("send_error") or ""),
                "send_error_type": str(run.get("send_error_type") or ""),
                "send_unknown_at": str(run.get("send_unknown_at") or ""),
                "updated_at": run.get("updated_at", ""),
            }
        )
        if status == "SENT":
            counts["sent"] += 1
        elif run.get("prompt_hold") or run.get("send_hold"):
            counts["held"] += 1
        elif status in ("IMAGE_READY", "READY_TO_SEND"):
            counts["generated"] += 1
        elif status == "FAILED":
            counts["failed"] += 1
        else:
            counts["pending"] += 1

    upcoming = []
    for card in cards:
        if selected_date != now.date() or card["status"] not in ("IMAGE_READY", "READY_TO_SEND"):
            continue
        if card["sent_at"]:
            continue
        if not card["wechat_send_enabled"] or card["send_hold"]:
            continue
        try:
            hour, minute = card["send_time"].split(":")
            send_at = now.replace(
                hour=int(hour),
                minute=int(minute),
                second=0,
                microsecond=0,
            )
        except Exception:
            send_at = now.replace(hour=8, minute=30, second=0, microsecond=0)
        upcoming.append((send_at, card["group_name"]))
    next_send = ""
    if upcoming:
        earliest, name = min(upcoming, key=lambda item: item[0])
        next_send = f"{earliest.strftime('%H:%M')}（{name}）"

    runtime_status = {"overall_status": "not_started", "summary": {}}
    store_root = getattr(store, "root", None)
    if isinstance(store_root, Path):
        runtime_path = store_root.parent / "runtime" / selected_run_date / "status.json"
        try:
            value = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                runtime_status = {
                    "overall_status": str(value.get("overall_status") or "needs_attention"),
                    "summary": value.get("summary") if isinstance(value.get("summary"), dict) else {},
                    "updated_at": str(value.get("updated_at") or ""),
                }
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass

    return {
        "today": selected_run_date,
        "run_date": selected_run_date,
        "should_run": window.should_run,
        "period_start": window.period_start_str(),
        "period_end": window.period_end_str(),
        "enabled_groups": len(cards),
        "counts": counts,
        "next_send": next_send,
        "daily_status": runtime_status,
        "cards": cards,
    }


@router.get("/runs")
def list_runs(
    settings: Settings = Depends(get_settings),
    run_date: str | None = None,
    include_files: bool = False,
):
    if run_date is not None:
        _validate_run_date(run_date)
    store = _store(settings)
    runs = store.list_runs(run_date)
    if include_files:
        for run in runs:
            run["files"] = _run_files(
                store,
                str(run.get("group_name") or ""),
                str(run.get("run_date") or ""),
            )
    return {"runs": runs, "total": len(runs)}


def _run_files(store, group: str, run_date: str) -> list[str]:
    try:
        _validate_run_date(run_date)
        group_dir = _safe_group_dir(store, group, run_date)
    except HTTPException:
        return []
    if not group_dir.exists():
        return []
    return sorted(
        path.name
        for path in group_dir.glob("*")
        if path.is_file() and path.name in ALLOWED_FILES
    )


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
    "send_state",
    "send_hold",
    "send_hold_reason",
    "send_error",
    "send_error_type",
    "send_unknown_at",
    "send_unknown_stage",
    "text_sent_at",
    "image_sent_at",
    "text_submitted_at",
    "image_submitted_at",
    "verification_level",
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
        "display_name": (
            group.display_name or group.wechat_group_name or f"群 {group.id}"
        ),
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
    entries = {
        group.id: _archive_group_entry(group)
        for group in db_groups
        if group.id is not None
    }
    groups_by_id = {
        group.id: group for group in db_groups if group.id is not None
    }
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
                "display_name": (
                    _archive_text(raw_run.get("group_name")) or "历史遗留群"
                ),
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
            key=lambda item: (
                _archive_text(item.get("run_date")),
                _archive_text(item.get("updated_at")),
            ),
            reverse=True,
        )
        entry["run_count"] = len(entry["runs"])
        entry["run_dates"] = sorted(
            {
                _archive_text(item.get("run_date"))
                for item in entry["runs"]
                if item.get("run_date")
            },
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
        key=lambda entry: (
            entry["runs"][0].get("run_date", "") if entry["runs"] else ""
        ),
        reverse=True,
    )
    return {
        "groups": [*active, *deleted, *orphaned],
        "active_count": len(active),
        "trash_count": len(deleted) + len(orphaned),
    }


@router.get("/runs/{group}/{run_date}")
def run_detail(
    group: str,
    run_date: str,
    settings: Settings = Depends(get_settings),
):
    _validate_run_date(run_date)
    store = _store(settings)
    _safe_group_dir(store, group, run_date)
    run = store.load_run(group, run_date)
    return {"run": run, "files": _run_files(store, group, run_date)}


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
    path = _safe_group_dir(_store(settings), group, run_date) / file_name
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=file_name)
