"""V1 兼容邮件任务。"""

from __future__ import annotations

from sqlmodel import Session

from app.core.logging import get_logger
from app.db import repository as repo
from app.services.email_service import EmailService

logger = get_logger("groupbrief.scheduler")


def run_email_job() -> dict:
    from app.config.settings import get_settings

    settings = get_settings()
    with Session(repo.engine) as session:
        service = EmailService(settings)
        ok, detail = service.send(session)
        logger.info("自动邮件结果：ok=%s detail=%s", ok, detail)
        return {"status": "sent" if ok else "failed", "detail": detail}
