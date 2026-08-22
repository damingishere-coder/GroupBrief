"""V1 兼容生成任务：每天读取前一日消息并生成报告。"""

from __future__ import annotations

from sqlmodel import Session

from app.core.logging import get_logger
from app.db import repository as repo
from app.services.report_service import ReportService
from app.services.generation_runtime import GenerationBusyError

logger = get_logger("groupbrief.scheduler")


def run_generate_job() -> dict:
    with Session(repo.engine) as session:
        service = ReportService()
        try:
            run = service.generate(session, trigger_type="auto")
        except GenerationBusyError as exc:
            logger.info("自动生成未领取：%s", exc)
            return {"status": "already_running", "detail": str(exc)}
        logger.info("自动生成完成：run=%s status=%s", run.id, run.status)
        return {"status": run.status, "run_id": run.id, "report_date": run.report_date}
