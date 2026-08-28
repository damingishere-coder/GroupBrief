"""群级历史数据源与 AI Provider 白名单。"""

from __future__ import annotations

import shutil

from app.config.settings import Settings
from app.db.models import Group
from app.ranking.policies import (
    normalize_ranking_policy,
    normalize_sender_name_policy,
)

HISTORY_PROVIDERS = frozenset({"", "wechat_data_analysis", "wechat_cli"})
AI_PROVIDERS = frozenset({"codex", "deepseek"})


def normalize_history_provider(value: object) -> str:
    name = str(value or "").strip().lower().replace("-", "_")
    if name not in HISTORY_PROVIDERS:
        raise ValueError(f"不支持的历史数据源：{value}")
    return name


def provider_catalog(settings: Settings) -> dict:
    codex_model = str(settings.codex_summary_model or "gpt-5.6-sol").strip()
    deepseek_model = str(settings.ai_model or "deepseek-v4-flash").strip()
    codex_binary = shutil.which(settings.codex_path or "codex")
    return {
        "history": [
            {
                "provider": "wechat_data_analysis",
                "label": "WeChatDataAnalysis",
                "available": True,
                "capabilities": ["history"],
            },
            {
                "provider": "wechat_cli",
                "label": "wechat-cli",
                "available": bool(shutil.which(settings.wechat_cli_path or "wechat-cli")),
                "capabilities": ["history"],
            },
        ],
        "ai": [
            {
                "provider": "codex",
                "label": "Codex GPT",
                "available": bool(codex_binary),
                "models": [codex_model],
                "capabilities": ["summary", "prompt"],
            },
            {
                "provider": "deepseek",
                "label": "DeepSeek",
                "available": bool(settings.ai_api_key),
                "models": [deepseek_model],
                "capabilities": ["summary", "prompt"],
            },
        ],
    }


def resolve_group_ai_settings(
    settings: Settings,
    group: Group,
    *,
    capability: str,
) -> tuple[Settings, dict]:
    if capability not in {"summary", "prompt"}:
        raise ValueError(f"未知 AI 能力：{capability}")
    provider_field = f"{capability}_provider"
    model_field = f"{capability}_model"
    configured_provider = str(getattr(group, provider_field, "") or "").strip().lower()
    configured_model = str(getattr(group, model_field, "") or "").strip()
    inherited = not configured_provider and not configured_model
    provider = configured_provider
    if not provider and configured_model:
        # 旧数据库只有 model 字段：按当前受控白名单推导 Provider，不混用未知值。
        if configured_model == str(settings.codex_summary_model or "gpt-5.6-sol").strip():
            provider = "codex"
        elif configured_model == str(settings.ai_model or "deepseek-v4-flash").strip():
            provider = "deepseek"
    provider = provider or str(settings.summary_provider_primary or "").strip().lower()
    if provider in {"codex_gpt", "gpt"}:
        provider = "codex"
    if provider not in AI_PROVIDERS:
        raise ValueError(f"不支持的 {capability} Provider：{configured_provider or provider}")

    default_model = (
        str(settings.codex_summary_model or "gpt-5.6-sol").strip()
        if provider == "codex"
        else str(settings.ai_model or "deepseek-v4-flash").strip()
    )
    model = configured_model or default_model
    allowed = {
        str(row_model)
        for row in provider_catalog(settings)["ai"]
        if row["provider"] == provider
        for row_model in row["models"]
    }
    if model not in allowed:
        raise ValueError(f"模型 {model} 不在 {provider} 已验证白名单中")

    updates = {
        "summary_provider_primary": provider,
        "summary_provider_fallback": (
            str(settings.summary_provider_fallback or "").strip().lower()
            if provider == "codex"
            else ""
        ),
    }
    if provider == "codex":
        updates["codex_summary_model"] = model
    else:
        updates["ai_model"] = model
    resolved = settings.model_copy(update=updates)
    return resolved, {
        "provider": provider,
        "model": model,
        "inherited": inherited and not configured_model,
        "capability": capability,
    }


def validate_group_provider_values(
    values: dict,
    settings: Settings,
    *,
    base: Group | None = None,
) -> dict:
    normalized = dict(values)
    if "provider_preference" in normalized:
        normalized["provider_preference"] = normalize_history_provider(
            normalized.get("provider_preference")
        )
    schedule_rule = normalized.get("schedule_rule")
    if schedule_rule is not None and schedule_rule not in {
        "weekday_default",
        "daily_previous_day",
    }:
        raise ValueError(f"不支持的统计周期规则：{schedule_rule}")
    if "ranking_count_policy" in normalized:
        normalized["ranking_count_policy"] = normalize_ranking_policy(
            normalized.get("ranking_count_policy")
        )
    if "sender_name_policy" in normalized:
        normalized["sender_name_policy"] = normalize_sender_name_policy(
            normalized.get("sender_name_policy")
        )
    candidate_values = (
        {key: getattr(base, key) for key in Group.model_fields if hasattr(base, key)}
        if base is not None
        else {}
    )
    candidate_values.update(
        {
            key: value
            for key, value in normalized.items()
            if key in Group.model_fields
        }
    )
    candidate = Group(**candidate_values)
    for capability in ("summary", "prompt"):
        if any(
            field in normalized
            for field in (f"{capability}_provider", f"{capability}_model")
        ):
            resolve_group_ai_settings(settings, candidate, capability=capability)
    return normalized
