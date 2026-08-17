"""执行记录 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import repository as repo

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("")
def list_runs(limit: int = 50, session: Session = Depends(repo.get_session)):
    runs = repo.find_runs(session, limit)
    result = []
    for run in runs:
        result.append(
            {
                "id": run.id,
                "report_date": run.report_date,
                "range_start": run.range_start,
                "range_end": run.range_end,
                "trigger_type": run.trigger_type,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error_message": run.error_message,
            }
        )
    return result
