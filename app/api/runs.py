"""执行记录 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import repository as repo

router = APIRouter(prefix="/api/runs", tags=["runs"], deprecated=True)


@router.get("/{run_id}")
def run_detail(run_id: int, session: Session = Depends(repo.get_session)):
    """单个 run 详情（含每个群的 group_runs 状态）。"""
    from fastapi import HTTPException
    from sqlmodel import select

    from app.db.models import Group, GroupRun

    run = repo.get_run(session, run_id)
    if not run:
        raise HTTPException(404, "run 不存在")

    group_runs = session.exec(
        select(GroupRun).where(GroupRun.run_id == run_id)
    ).all()
    details = []
    for gr in group_runs:
        group = session.get(Group, gr.group_id) if gr.group_id is not None else None
        if group:
            group_name = group.display_name or group.wechat_group_name
        elif gr.identity_state == "orphaned":
            group_name = f"历史群（旧 ID {gr.legacy_group_id}）"
        else:
            group_name = "未知群"
        details.append(
            {
                "id": gr.id,
                "group_id": gr.group_id,
                "legacy_group_id": gr.legacy_group_id,
                "identity_state": gr.identity_state,
                "orphan_reason": gr.orphan_reason,
                "group_name": group_name,
                "provider_used": gr.provider_used,
                "message_count": gr.message_count,
                "speaker_count": gr.speaker_count,
                "ranking_status": gr.ranking_status,
                "prompt_status": gr.prompt_status,
                "error_message": gr.error_message,
            }
        )

    return {
        "id": run.id,
        "report_date": run.report_date,
        "range_start": run.range_start,
        "range_end": run.range_end,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_message": run.error_message,
        "group_runs": details,
    }


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
