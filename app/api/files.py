"""文件管理 API：查看 output 目录结构（V2 Handoff 交接接口）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse

from app.services.handoff_service import HandoffService

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("/dates")
def dates():
    service = HandoffService()
    return service.list_output_dates()


@router.get("/{report_date}")
def list_day(report_date: str):
    service = HandoffService()
    return service.list_group_outputs(report_date)


@router.get("/{report_date}/{group_dir}/raw/{filename}")
def raw_file(report_date: str, group_dir: str, filename: str):
    service = HandoffService()
    day_dir = service.settings.output_dir / report_date
    file = (day_dir / group_dir / filename).resolve()
    if not file.is_file() or not str(file).startswith(str(day_dir.resolve())):
        return PlainTextResponse("not found", status_code=404)
    if filename.endswith(".json"):
        return PlainTextResponse(file.read_text(encoding="utf-8"), media_type="application/json")
    return FileResponse(file)
