"""09:00 自动邮件任务：每天只发送一封，包含所有启用群。"""

from __future__ import annotations

from sqlmodel import Session

from app.core.logging import get_logger
from app.db import repository as repo
from app.scheduler.calendar_rules import get_report_window
from app.services.email_service import EmailService

logger = get_logger("groupbrief.scheduler")


def run_email_job() -> dict:
    from app.config.settings import get_settings

    settings = get_settings()
    window = get_report_window(timezone=settings.app_timezone)
    if not window.should_run:
        logger.info("周日：自动邮件跳过")
        return {"status": "skipped", "reason": "周日不执行"}

    with Session(repo.engine) as session:
        service = EmailService(settings)
        ok, detail = service.send(session)
        logger.info("自动邮件结果：ok=%s detail=%s", ok, detail)
        return {"status": "sent" if ok else "failed", "detail": detail}
