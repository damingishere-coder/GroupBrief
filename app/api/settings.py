"""设置 API。

敏感值（API Key、SMTP 密码）不回显完整内容。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo

router = APIRouter(prefix="/api/settings", tags=["settings"])

SENSITIVE_KEYS = {
    "ai_api_key",
    "email_smtp_password",
    "email_smtp_user",
    "email_from",
    "wechat_mcp_token",
}

EDITABLE_KEYS = {
    "history_provider_primary",
    "history_provider_fallback",
    "history_provider_mock_enabled",
    "wechat_data_dir",
    "wechat_export_dir",
    "wechat_cli_path",
    "wechat_mcp_url",
    "wechat_mcp_token",
    "wechat_mcp_account",
    "wechat_mcp_timeout_seconds",
    "ai_provider",
    "ai_base_url",
    "ai_api_key",
    "ai_model",
    "ai_timeout_seconds",
    "ai_max_retries",
    "chunk_message_count",
    "max_context_chars",
    "email_enabled",
    "email_recipient",
    "email_from",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_user",
    "email_smtp_password",
    "email_use_ssl",
    "email_send_partial_report",
    "schedule_generate_time",
    "schedule_email_time",
}

ALL_KEYS = EDITABLE_KEYS | SENSITIVE_KEYS


class SettingsPayload(BaseModel):
    values: dict[str, str]


@router.get("")
def get_settings(session: Session = Depends(repo.get_session)):
    data: dict[str, str] = {}
    for key in sorted(ALL_KEYS):
        value = repo.get_setting_value(session, key)
        if key in SENSITIVE_KEYS:
            data[key] = "******" if value else ""
        else:
            data[key] = value
    return data


@router.put("")
def update_settings(payload: SettingsPayload, session: Session = Depends(repo.get_session)):
    applied: dict[str, str] = {}
    for key, value in payload.values.items():
        if key not in EDITABLE_KEYS:
            continue
        if key in SENSITIVE_KEYS and value in ("", "******"):
            continue  # 不覆盖已有密钥
        repo.set_setting_value(session, key, value)
        applied[key] = value
    if applied:
        # 让本次修改立即在运行中的 Settings 实例生效（类型安全、忽略掩码值）。
        from app.config.settings import get_settings

        get_settings().apply_runtime_values(applied)
    return {"ok": True}
