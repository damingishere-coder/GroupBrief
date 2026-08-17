"""08:45 自动生成任务：读取 → 整理 → 排行 → Prompt → 保存文件。"""

from __future__ import annotations

from sqlmodel import Session

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.scheduler.calendar_rules import get_report_window
from app.services.report_service import ReportService

logger = get_logger("groupbrief.scheduler")


def run_generate_job() -> dict:
    settings = get_settings()
    window = get_report_window(timezone=settings.app_timezone)
    if not window.should_run:
        logger.info("周日：自动生成跳过")
        return {"status": "skipped", "reason": "周日不执行"}

    with Session(repo.engine) as session:
        service = ReportService()
        run = service.generate(session, trigger_type="auto")
        logger.info("自动生成完成：run=%s status=%s", run.id, run.status)
        return {"status": run.status, "run_id": run.id, "report_date": run.report_date}
