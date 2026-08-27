from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.db.models import Group
from app.services.group_provider_config import (
    normalize_history_provider,
    resolve_group_ai_settings,
    validate_group_provider_values,
)


def test_history_provider_alias_is_normalized_and_unknown_is_rejected():
    assert normalize_history_provider("wechat-cli") == "wechat_cli"
    with pytest.raises(ValueError, match="不支持"):
        normalize_history_provider("free-form-provider")


def test_group_ai_config_inherits_global_and_rejects_unknown_model():
    settings = Settings(
        _env_file=None,
        summary_provider_primary="codex",
        codex_summary_model="gpt-5.6-sol",
    )
    group = Group(display_name="群", prompt_provider="", prompt_model="")
    resolved, meta = resolve_group_ai_settings(settings, group, capability="prompt")
    assert meta == {
        "provider": "codex",
        "model": "gpt-5.6-sol",
        "inherited": True,
        "capability": "prompt",
    }
    assert resolved.summary_provider_primary == "codex"

    group.prompt_provider = "codex"
    group.prompt_model = "unknown-model"
    with pytest.raises(ValueError, match="白名单"):
        resolve_group_ai_settings(settings, group, capability="prompt")


def test_group_configuration_accepts_only_supported_schedule_rules():
    settings = Settings(_env_file=None)
    assert validate_group_provider_values(
        {"schedule_rule": "daily_previous_day"}, settings
    )["schedule_rule"] == "daily_previous_day"
    with pytest.raises(ValueError, match="统计周期"):
        validate_group_provider_values({"schedule_rule": "cron:*"}, settings)
