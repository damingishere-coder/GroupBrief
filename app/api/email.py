"""邮件 API：预览 + 手动发送。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.services.email_service import EmailService
from app.services.legacy_v1_policy import (
    LegacyV1WriteBlockedError,
    require_legacy_v1_write,
)

router = APIRouter(prefix="/api/email", tags=["email"], deprecated=True)


@router.get("/preview")
def preview(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    service = EmailService(settings)
    result = service.build_email(session)
    return {
        "subject": result.subject,
        "body": result.body,
        "blocks": len(result.blocks),
        "missing": result.missing,
    }


@router.post("/send")
def send(
    session: Session = Depends(repo.get_session),
    settings: Settings = Depends(get_settings),
):
    try:
        require_legacy_v1_write(
            settings,
            operation="email.send",
            replacement="V2 每日任务或 scripts/send_daily_email.py",
        )
    except LegacyV1WriteBlockedError as exc:
        raise HTTPException(status_code=410, detail=exc.as_detail()) from exc
    service = EmailService(settings)
    ok, detail = service.send(session)
    return {"ok": ok, "detail": detail}
