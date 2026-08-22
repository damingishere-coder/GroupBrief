"""P1 测试：Provider 框架、降级、fixtures 读取。"""

from datetime import datetime, timezone

from app.providers.history.base import ProviderStatus
from app.providers.history.mock import MockProvider
from app.providers.history.registry import build_providers, check_all_health
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
    service = HistoryService()
    outcome = service.fetch("group-a", "示例UED-4群🤘", datetime(2026, 8, 10), datetime(2026, 8, 17))
    assert outcome.status == ProviderStatus.OK
    assert len(outcome.messages) > 0
    # 主/备不可用时降级到 mock
    assert outcome.provider in ("wechat_data_analysis", "wechat_cli", "mock")


def test_all_health_contains_expected():
    health = check_all_health()
    assert "wechat_data_analysis" in health
    assert "wechat_cli" in health
    assert "mock" in health
    assert health["mock"].ok


def test_providers_order():
    providers = build_providers()
    names = [p.name for p in providers]
    assert names[0] == "wechat_data_analysis"
    assert "mock" in names
