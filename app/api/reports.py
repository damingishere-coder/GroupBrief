"""群报生成 / 报告 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateRequest(BaseModel):
    report_date: str = ""  # YYYY-MM-DD，空 = 今天
    group_id: int | None = None  # None = 全部启用群
    force: bool = False


@router.post("/generate")
def generate(payload: GenerateRequest, session: Session = Depends(repo.get_session)):
    # P2/P3 阶段接入 report_service
    return {"queued": False, "message": "P0 骨架：生成引擎尚未接入"}


@router.get("/latest")
def latest(session: Session = Depends(repo.get_session)):
    reports = repo.find_recent_reports(session, 20)
    return [
        {
            "id": r.id,
            "group_run_id": r.group_run_id,
            "ranking_text": r.ranking_text,
            "prompt_text": r.prompt_text,
            "ranking_file": r.ranking_file,
            "prompt_file": r.prompt_file,
            "poster_file": r.poster_file,
            "poster_status": r.poster_status,
            "email_status": r.email_status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]
