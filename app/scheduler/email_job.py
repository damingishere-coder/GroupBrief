"""V1 兼容邮件任务。"""

from __future__ import annotations

from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.scheduler.outcome import attach_outcome
from app.services.email_service import EmailService
from app.services.legacy_v1_policy import (
    LEGACY_V1_WRITE_BLOCKED,
    LegacyV1WriteBlockedError,
    require_legacy_v1_write,
)

logger = get_logger("groupbrief.scheduler")


def run_email_job(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        require_legacy_v1_write(
            settings,
            operation="scheduler.email.send",
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
        service = EmailService(settings)
        ok, detail = service.send(session)
        logger.info("自动邮件结果：ok=%s detail=%s", ok, detail)
        return {"status": "sent" if ok else "failed", "detail": detail}
