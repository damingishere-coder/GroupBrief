"""群报生成 / 报告 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.db.models import GroupRun, Report
from app.services.report_service import ReportService

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateRequest(BaseModel):
    report_date: str = ""  # YYYY-MM-DD，空 = 今天
    group_id: int | None = None  # None = 全部启用群
    force: bool = False


class PromptUpdate(BaseModel):
    text: str


@router.post("/generate")
def generate(payload: GenerateRequest, session: Session = Depends(repo.get_session)):
    service = ReportService()
    group = None
    if payload.group_id is not None:
        group = repo.get_group(session, payload.group_id)
        if not group:
            raise HTTPException(404, "群不存在")
    run = service.generate(
        session,
        group=group,
        report_date=payload.report_date or None,
        trigger_type="manual",
        force=payload.force,
    )
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
def update_prompt(report_id: int, payload: PromptUpdate, session: Session = Depends(repo.get_session)):
    report = session.get(Report, report_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    report.prompt_text = payload.text
    report.updated_at = datetime.now()
    repo.save_report(session, report)
    return {"ok": True}
