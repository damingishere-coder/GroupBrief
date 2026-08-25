"""设置 API 的 Provider/config fail-closed 测试；不写真实数据库。"""

import pytest
from fastapi import HTTPException

from app.api import settings as settings_api
from app.config.settings import Settings


def _invoke(monkeypatch, values: dict[str, str]):
    runtime = Settings(_env_file=None)
    writes: list[tuple[str, str]] = []
    monkeypatch.setattr(settings_api, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(
        settings_api.repo,
        "set_setting_value",
        lambda _session, key, value: writes.append((key, value)),
    )
    result = settings_api.update_settings(
        settings_api.SettingsPayload(values=values),
        session=object(),
    )
    return result, runtime, writes


@pytest.mark.parametrize(
    "values, message",
    [
        ({"summary_provider_primary": "typo"}, "总结主 Provider"),
        ({"summary_provider_fallback": "typo"}, "总结备用 Provider"),
        ({"wechat_sender_mode": "typo"}, "微信发送 Provider"),
        ({"email_enabled": "true"}, "SMTP 主机"),
        ({"email_use_ssl": "maybe"}, "设置值类型无效"),
    ],
)
def test_invalid_provider_or_email_config_is_rejected_before_write(
    monkeypatch,
    values,
    message,
):
    writes = []
    runtime = Settings(_env_file=None)
    monkeypatch.setattr(settings_api, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr(
        settings_api.repo,
        "set_setting_value",
        lambda _session, key, value: writes.append((key, value)),
    )

    with pytest.raises(HTTPException, match=message) as exc_info:
        settings_api.update_settings(
            settings_api.SettingsPayload(values=values),
            session=object(),
        )

    assert exc_info.value.status_code == 422
    assert not writes


def test_test_only_and_legacy_provider_switches_are_not_api_editable(monkeypatch):
    result, runtime, writes = _invoke(
        monkeypatch,
        {
            "history_provider_primary": "typo",
            "history_provider_fallback": "mock",
            "history_provider_mock_enabled": "true",
            "allow_test_providers": "true",
            "ai_provider": "anything",
        },
    )

    assert result == {"ok": True}
    assert runtime.allow_test_providers is False
    assert runtime.history_provider_primary == "wechat_data_analysis"
    assert runtime.history_provider_fallback == "wechat_cli"
    assert runtime.history_provider_mock_enabled is False
    assert not writes


def test_valid_settings_are_persisted_and_applied_after_validation(monkeypatch):
    result, runtime, writes = _invoke(
        monkeypatch,
        {
            "summary_provider_primary": "deepseek",
            "summary_provider_fallback": "disabled",
            "wechat_sender_mode": "legacy_cli",
        },
    )

    assert result == {"ok": True}
    assert runtime.summary_provider_primary == "deepseek"
    assert runtime.summary_provider_fallback == "disabled"
    assert runtime.wechat_sender_mode == "legacy_cli"
    assert writes == [
        ("summary_provider_primary", "deepseek"),
        ("summary_provider_fallback", "disabled"),
        ("wechat_sender_mode", "legacy_cli"),
    ]
