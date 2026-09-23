"""设置 API。

敏感值（API Key、SMTP 密码）不回显完整内容。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.db import repository as repo
from app.config.settings import get_settings as get_runtime_settings
from app.providers.ai.codex import validate_summary_provider_config
from app.sender.wechat_native import validate_wechat_sender_mode
from app.services.email_service import email_delivery_config_error

router = APIRouter(prefix="/api/settings", tags=["settings"])

SENSITIVE_KEYS = {
    "ai_api_key",
    "email_smtp_password",
    "email_smtp_user",
    "email_from",
    "wechat_mcp_token",
}

EDITABLE_KEYS = {
    "knowledge_memory_call_budget",
    "knowledge_memory_input_budget",
    "knowledge_memory_output_budget",
    "wechat_data_dir",
    "wechat_export_dir",
    "wechat_cli_path",
    "wechat_mcp_url",
    "wechat_mcp_token",
    "wechat_mcp_account",
    "wechat_mcp_timeout_seconds",
    "wechat_mcp_range_timeout_seconds",
    "wechat_fetch_total_timeout_seconds",
    "wechat_contact_db_path",
    "summary_provider_primary",
    "summary_provider_fallback",
    "codex_summary_model",
    "codex_reasoning_effort",
    "codex_image_model",
    "codex_summary_timeout_seconds",
    "codex_summary_max_retries",
    "codex_summary_request_concurrency",
    "ai_base_url",
    "ai_api_key",
    "ai_model",
    "ai_timeout_seconds",
    "ai_max_retries",
    "max_context_chars",
    "generation_group_concurrency",
    "wechat_fetch_concurrency",
    "ai_request_concurrency",
    "codex_path",
    "codex_home",
    "codex_timeout_seconds",
    "codex_generated_images_dir",
    "image_generation_concurrency",
    "image_fallback_font_path",
    "wechat_sender_mode",
    "wechat_native_action_delay_seconds",
    "wechat_native_stage_timeout_seconds",
    "wechat_native_submit_timeout_seconds",
    "wechat_native_poll_interval_seconds",
    "wechat_native_mutex_timeout_seconds",
    "wechat_send_claim_seconds",
    "wechat_late_send_window_minutes",
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
    "schedule_send_time",
    "schedule_email_time",
    "schedule_startup_catchup_enabled",
}

ALL_KEYS = EDITABLE_KEYS | SENSITIVE_KEYS

_SUMMARY_CONFIG_KEYS = {"summary_provider_primary", "summary_provider_fallback", "codex_reasoning_effort"}
_EMAIL_CONFIG_KEYS = {
    "email_enabled",
    "email_recipient",
    "email_from",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_user",
    "email_smtp_password",
    "email_use_ssl",
}


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
    data["schedule_send_time"] = get_runtime_settings().schedule_send_time
    data["schedule_send_skip_dates"] = get_runtime_settings().schedule_send_skip_dates
    return data


@router.put("")
def update_settings(payload: SettingsPayload, session: Session = Depends(repo.get_session)):
    requested: dict[str, str] = {}
    for key, value in payload.values.items():
        if key not in EDITABLE_KEYS:
            continue
        if key in SENSITIVE_KEYS and value in ("", "******"):
            continue  # 不覆盖已有密钥
        requested[key] = value
    if requested:
        runtime_settings = get_runtime_settings()
        candidate = runtime_settings.model_copy(deep=True)
        converted = set(candidate.apply_runtime_values(requested))
        rejected = sorted(set(requested) - converted)
        if rejected:
            raise HTTPException(status_code=422, detail=f"设置值类型无效：{', '.join(rejected)}")
        changed = set(requested)
        try:
            if "schedule_send_time" in changed and not re.fullmatch(
                r"(?:[01]\d|2[0-3]):[0-5]\d", candidate.schedule_send_time
            ):
                raise ValueError("发送时间必须是有效的 HH:MM（00:00—23:59）")
            if changed & {'knowledge_memory_call_budget','knowledge_memory_input_budget','knowledge_memory_output_budget'}:
                if not (0<=candidate.knowledge_memory_call_budget<=20 and 0<=candidate.knowledge_memory_input_budget<=200000 and 0<=candidate.knowledge_memory_output_budget<=40000):
                    raise ValueError('记忆预算超出允许范围：每日调用 0—20，输入 0—200000，输出 0—40000')
            if changed & _SUMMARY_CONFIG_KEYS:
                validate_summary_provider_config(candidate)
            if "wechat_sender_mode" in changed:
                validate_wechat_sender_mode(candidate)
            if candidate.email_enabled and changed & _EMAIL_CONFIG_KEYS:
                email_error = email_delivery_config_error(candidate)
                if email_error:
                    raise ValueError(email_error)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # 先完整校验，再写入持久化设置，避免部分无效配置已经入库。
        for key, value in requested.items():
            repo.set_setting_value(session, key, value)
        runtime_settings.apply_runtime_values(requested)
        if "schedule_send_time" in requested:
            from app.scheduler.manager import reschedule_send_batch

            reschedule_send_batch(runtime_settings)
    return {"ok": True}
