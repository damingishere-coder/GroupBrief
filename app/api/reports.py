"""群报生成 / 报告 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import GroupRun, Report
from app.services.report_service import ReportService
from app.services.generation_runtime import GenerationBusyError
from app.services.legacy_v1_policy import (
    LegacyV1WriteBlockedError,
    require_legacy_v1_write,
)

router = APIRouter(prefix="/api/reports", tags=["reports"], deprecated=True)


class GenerateRequest(BaseModel):
    report_date: str = ""  # YYYY-MM-DD，空 = 今天
    group_id: int | None = None  # None = 全部启用群
    force: bool = False


class PromptUpdate(BaseModel):
    text: str


def _require_write(settings: Settings, *, operation: str, replacement: str) -> None:
    try:
        require_legacy_v1_write(
            settings,
            operation=operation,
            replacement=replacement,
        )
    except LegacyV1WriteBlockedError as exc:
        raise HTTPException(status_code=410, detail=exc.as_detail()) from exc


@router.post("/generate")
def generate(
    payload: GenerateRequest,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    _require_write(
        settings,
        operation="report.generate",
        replacement="POST /api/v2/pipeline/generate",
    )
    service = ReportService(settings=settings)
    group = None
    if payload.group_id is not None:
        group = repo.get_active_group(session, payload.group_id)
        if not group:
            raise HTTPException(404, "群不存在")
    try:
        run = service.generate(
            session,
            group=group,
            report_date=payload.report_date or None,
            trigger_type="manual",
            force=payload.force,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "run_id": run.id,
        "status": run.status,
        "report_date": run.report_date,
        "range_start": run.range_start,
        "range_end": run.range_end,
        "error_message": run.error_message,
    }


@router.get("/latest")
def latest(session: Session = Depends(repo.get_session)):
    reports = repo.find_recent_reports(session, 50)
    result = []
    for r in reports:
        group_run = session.get(GroupRun, r.group_run_id)
        result.append(
            {
                "id": r.id,
                "group_run_id": r.group_run_id,
                "group_id": group_run.group_id if group_run else None,
                "legacy_group_id": group_run.legacy_group_id if group_run else None,
                "identity_state": group_run.identity_state if group_run else "unresolved",
                "orphan_reason": group_run.orphan_reason if group_run else "group_run_missing",
                "ranking_text": r.ranking_text,
                "prompt_text": r.prompt_text,
                "ranking_file": r.ranking_file,
                "prompt_file": r.prompt_file,
                "poster_file": r.poster_file,
                "poster_status": r.poster_status,
                "email_status": r.email_status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return result


@router.put("/{report_id}/prompt")
def update_prompt(
    report_id: int,
    payload: PromptUpdate,
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    _require_write(
        settings,
        operation="report.prompt.update",
        replacement="PUT /api/v2/runs/{group}/{run_date}/prompt",
    )
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    report.prompt_text = payload.text
    report.updated_at = datetime.now()
    repo.save_report(session, report)
    return {"ok": True}
