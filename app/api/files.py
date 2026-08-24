"""文件管理 API：查看 output 目录结构（V2 Handoff 交接接口）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.config.settings import Settings, get_settings
from app.core.path_security import resolve_within
from app.services.handoff_service import HandoffService

router = APIRouter(prefix="/api/files", tags=["files"])

ALLOWED_RAW_FILES = frozenset(
    {
        "ranking.txt",
        "image_prompt.txt",
        "meta.json",
        "normalized_messages.json",
        "handoff.json",
        "messages.json",
        "ranking.json",
        "run.json",
        "image_prompt.original.txt",
        "daily_image.png",
        "daily_image.previous.png",
    }
)


@router.get("/dates")
def dates(settings: Settings = Depends(get_settings)):
    service = HandoffService(settings)
    return service.list_output_dates()


@router.get("/{report_date}")
def list_day(report_date: str, settings: Settings = Depends(get_settings)):
    service = HandoffService(settings)
    try:
        return service.list_group_outputs(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{report_date}/{group_dir}/raw/{filename}")
def raw_file(
    report_date: str,
    group_dir: str,
    filename: str,
    settings: Settings = Depends(get_settings),
):
    if filename not in ALLOWED_RAW_FILES:
        raise HTTPException(status_code=400, detail=f"不允许访问的文件：{filename}")
    service = HandoffService(settings)
    try:
        day_dir = service.output_day_dir(report_date)
        file = resolve_within(day_dir, group_dir, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not file.is_file():
        return PlainTextResponse("not found", status_code=404)
    if filename.endswith(".json"):
        return PlainTextResponse(file.read_text(encoding="utf-8"), media_type="application/json")
    return FileResponse(file)
