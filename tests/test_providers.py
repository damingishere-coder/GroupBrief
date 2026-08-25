"""P1 测试：Provider 框架、降级、fixtures 读取。"""

from datetime import datetime, timezone

import pytest

from app.config.settings import Settings
from app.providers.history.base import ProviderStatus
from app.providers.history.mock import MockProvider
from app.providers.history.registry import (
    ProviderConfigurationError,
    build_providers,
    check_all_health,
)
from app.services.history_service import HistoryService

TZ = timezone.utc


def test_mock_health_ok():
    mock = MockProvider()
    health = mock.health_check()
    assert health.ok


def test_mock_list_groups():
    mock = MockProvider()
    groups = mock.list_groups()
    assert len(groups) >= 2
    assert groups[0].group_id == "group-a"


def test_mock_fetch_range():
    mock = MockProvider()
    start = datetime(2026, 8, 10, 0, 0, 0)
    end = datetime(2026, 8, 17, 23, 59, 59)
    result = mock.fetch_messages("group-a", start, end)
    assert result.status == ProviderStatus.OK
    assert len(result.messages) > 500
    names = {m.sender_name for m in result.messages}
    assert "成员 A01" in names
    # 系统消息也会返回（排行榜层过滤）
    types = {m.message_type for m in result.messages}
    assert "text" in types and "image" in types


def test_mock_fetch_empty_group():
    mock = MockProvider()
    result = mock.fetch_messages("no_such_group", datetime(2026, 8, 10), datetime(2026, 8, 17))
    assert result.status == ProviderStatus.GROUP_NOT_FOUND


def test_fallback_to_mock():
    service = HistoryService(
        Settings(
            _env_file=None,
            allow_test_providers=True,
            history_provider_mock_enabled=True,
        )
    )
    outcome = service.fetch("group-a", "示例UED-4群🤘", datetime(2026, 8, 10), datetime(2026, 8, 17))
    assert outcome.status == ProviderStatus.OK
    assert len(outcome.messages) > 0
    # 主/备不可用时降级到 mock
    assert outcome.provider in ("wechat_data_analysis", "wechat_cli", "mock")


def test_all_health_contains_expected():
    health = check_all_health(
        Settings(
            _env_file=None,
            allow_test_providers=True,
            history_provider_mock_enabled=True,
        )
    )
    assert "wechat_data_analysis" in health
    assert "wechat_cli" in health
    assert "mock" in health
    assert health["mock"].ok


def test_providers_order():
    providers = build_providers(
        Settings(
            _env_file=None,
            allow_test_providers=True,
            history_provider_mock_enabled=True,
        )
    )
    names = [p.name for p in providers]
    assert names[0] == "wechat_data_analysis"
    assert "mock" in names


def test_production_default_does_not_register_mock():
    providers = build_providers(Settings(_env_file=None))
    assert [provider.name for provider in providers] == ["wechat_data_analysis", "wechat_cli"]


def test_stored_mock_flag_cannot_bypass_test_provider_gate():
    providers = build_providers(
        Settings(
            _env_file=None,
            allow_test_providers=False,
            history_provider_mock_enabled=True,
        )
    )
    assert "mock" not in [provider.name for provider in providers]


@pytest.mark.parametrize("field", ["history_provider_primary", "history_provider_fallback"])
def test_unknown_history_provider_is_configuration_error(field):
    settings = Settings(_env_file=None, **{field: "typo_provider"})
    with pytest.raises(ProviderConfigurationError, match="不支持的历史 Provider"):
        build_providers(settings)


def test_explicit_mock_provider_is_blocked_without_test_gate():
    settings = Settings(
        _env_file=None,
        history_provider_primary="mock",
        history_provider_fallback="",
        allow_test_providers=False,
    )
    with pytest.raises(ProviderConfigurationError, match="真实运行禁止"):
        build_providers(settings)


def test_registry_passes_the_supplied_settings_to_provider():
    settings = Settings(
        _env_file=None,
        history_provider_primary="wechat_cli",
        history_provider_fallback="",
        wechat_cli_path="C:/custom/wechat-cli.exe",
    )
    provider = build_providers(settings)[0]
    assert provider.cli_path == "C:/custom/wechat-cli.exe"
