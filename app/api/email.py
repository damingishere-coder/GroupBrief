"""邮件 API：预览 + 手动发送。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import repository as repo
from app.services.email_service import EmailService

router = APIRouter(prefix="/api/email", tags=["email"])


@router.get("/preview")
def preview(session: Session = Depends(repo.get_session)):
    service = EmailService()
    result = service.build_email(session)
    return {
        "subject": result.subject,
        "body": result.body,
        "blocks": len(result.blocks),
        "missing": result.missing,
    }


@router.post("/send")
def send(session: Session = Depends(repo.get_session)):
    service = EmailService()
    ok, detail = service.send(session)
    return {"ok": ok, "detail": detail}
