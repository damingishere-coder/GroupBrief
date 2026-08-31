"""V1 兼容生成任务：每天读取前一日消息并生成报告。"""

from __future__ import annotations

from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.scheduler.outcome import attach_outcome
from app.services.report_service import ReportService
from app.services.generation_runtime import GenerationBusyError
from app.services.legacy_v1_policy import (
    LEGACY_V1_WRITE_BLOCKED,
    LegacyV1WriteBlockedError,
    require_legacy_v1_write,
)

logger = get_logger("groupbrief.scheduler")


def run_generate_job(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        require_legacy_v1_write(
            settings,
            operation="scheduler.report.generate",
            replacement="daily_v2_generate_email",
        )
    except LegacyV1WriteBlockedError as exc:
        return attach_outcome(
            {
                "status": "blocked",
                "error_type": LEGACY_V1_WRITE_BLOCKED,
                "detail": str(exc),
            }
        )
    with Session(repo.engine) as session:
        service = ReportService(settings=settings)
        try:
            run = service.generate(session, trigger_type="auto")
        except GenerationBusyError as exc:
            logger.info("自动生成未领取：%s", exc)
            return {"status": "already_running", "detail": str(exc)}
        logger.info("自动生成完成：run=%s status=%s", run.id, run.status)
        return {"status": run.status, "run_id": run.id, "report_date": run.report_date}
