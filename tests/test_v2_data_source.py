"""V2 P1：WeChatDataAnalysis 数据源单元测试。

通过注入 FakeProvider（实现 V1 ChatHistoryProvider 接口）隔离真实 MCP，
验证 V2 数据源的：健康映射 / 群解析 / Message Schema / 错误类型。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.config.settings import Settings
from app.data_sources.base import DataSourceStatus
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.providers.history.base import (
    FetchResult,
    GroupInfo,
    ProviderHealth,
    ProviderStatus,
    RawMessage,
)
from app.v2.constants import (
    GROUP_NOT_FOUND,
    MESSAGE_FETCH_FAILED,
    WECHAT_DATA_UNAVAILABLE,
)


def _raw(**overrides) -> RawMessage:
    base = dict(
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id="wxid_1",
        sender_name="小明",
        timestamp=datetime(2026, 8, 17, 10, 0, 0),
        message_type="text",
        content="你好",
        source="fake",
        source_message_id="msg_1",
        content_hash="",
    )
    base.update(overrides)
    return RawMessage(**base)


class FakeProvider:
    """按用例脚本化的 V1 Provider 替身。"""

    name = "fake"

    def __init__(self):
        self.health = ProviderHealth("fake", ProviderStatus.OK, "ok")
        self.groups = [GroupInfo(group_id="g1@chatroom", group_name="测试群", member_count=5)]
        self.fetch = FetchResult("fake", "g1@chatroom", [_raw()], ProviderStatus.OK)

    def health_check(self) -> ProviderHealth:
        return self.health

    def list_groups(self) -> list[GroupInfo]:
        return self.groups

    def resolve_groups(self, query: str) -> list[GroupInfo]:
        return [g for g in self.groups if query in g.group_name]

    def fetch_messages(self, group_id, start_time, end_time) -> FetchResult:
        return self.fetch


def _make_source(fake: FakeProvider | None = None) -> WeChatDataAnalysisSource:
    return WeChatDataAnalysisSource(provider=fake or FakeProvider())


# ---------- health ----------


def test_health_ok():
    source = _make_source()
    h = source.health_check()
    assert h.status == DataSourceStatus.OK
    assert h.ok


def test_health_unavailable_maps():
    fake = FakeProvider()
    fake.health = ProviderHealth("fake", ProviderStatus.UNAVAILABLE, "未找到微信数据")
    h = _make_source(fake).health_check()
    assert h.status == DataSourceStatus.UNAVAILABLE
    assert not h.ok


# ---------- list / resolve ----------


def test_list_groups():
    groups = _make_source().list_groups()
    assert len(groups) == 1
    assert groups[0].group_id == "g1@chatroom"
    assert groups[0].group_name == "测试群"


def test_resolve_group_by_name():
    matched = _make_source().resolve_group("测试")
    assert len(matched) == 1
    assert matched[0].group_name == "测试群"


# ---------- fetch / schema ----------


def test_fetch_messages_schema():
    result = _make_source().fetch_messages("g1@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.OK
    assert result.error_type == ""
    assert len(result.messages) == 1
    m = result.messages[0]
    # 路线文档 Message Schema 至少字段
    assert m.message_id == "msg_1"
    assert m.group_id == "g1@chatroom"
    assert m.group_name == "测试群"
    assert m.sender_id == "wxid_1"
    assert m.sender_name == "小明"
    assert m.timestamp == datetime(2026, 8, 17, 10, 0, 0)
    assert m.message_type == "text"
    assert m.content == "你好"
    # 序列化 JSON 安全
    d = m.to_dict()
    assert d["timestamp"].startswith("2026-08-17")


def test_fetch_message_id_fallback_to_hash():
    fake = FakeProvider()
    fake.fetch = FetchResult("fake", "g1@chatroom", [_raw(source_message_id="")], ProviderStatus.OK)
    result = _make_source(fake).fetch_messages("g1@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert len(result.messages) == 1
    assert result.messages[0].message_id  # 非空（确定性 hash）


# ---------- 错误类型 ----------


def test_fetch_group_not_found():
    fake = FakeProvider()
    fake.fetch = FetchResult("fake", "g1@chatroom", [], ProviderStatus.GROUP_NOT_FOUND, "没有该群")
    result = _make_source(fake).fetch_messages("g1@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.GROUP_NOT_FOUND
    assert result.error_type == GROUP_NOT_FOUND


def test_fetch_empty_unknown_group_maps_to_group_not_found():
    # 上游返回空，且该群不在已知群列表 → GROUP_NOT_FOUND
    fake = FakeProvider()
    fake.fetch = FetchResult("fake", "gx@chatroom", [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
    result = _make_source(fake).fetch_messages("gx@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.GROUP_NOT_FOUND
    assert result.error_type == GROUP_NOT_FOUND


def test_fetch_empty_known_group_is_empty():
    # 上游返回空，且群已知（列表中存在）→ EMPTY_RESULT（该时段确实无消息）
    fake = FakeProvider()
    fake.groups = [GroupInfo(group_id="gx@chatroom", group_name="存在群", member_count=3)]
    fake.fetch = FetchResult("fake", "gx@chatroom", [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
    result = _make_source(fake).fetch_messages("gx@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.EMPTY_RESULT
    assert result.error_type == ""


def test_fetch_read_failed_maps_to_message_fetch_failed():
    fake = FakeProvider()
    fake.fetch = FetchResult("fake", "g1@chatroom", [], ProviderStatus.READ_FAILED, "MCP 读取失败")
    result = _make_source(fake).fetch_messages("g1@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.READ_FAILED
    assert result.error_type == MESSAGE_FETCH_FAILED


def test_fetch_unavailable_maps_to_wechat_data_unavailable():
    fake = FakeProvider()
    fake.fetch = FetchResult("fake", "g1@chatroom", [], ProviderStatus.UNAVAILABLE, "不可用")
    result = _make_source(fake).fetch_messages("g1@chatroom", datetime(2026, 8, 17), datetime(2026, 8, 17))
    assert result.status == DataSourceStatus.READ_FAILED
    assert result.error_type == WECHAT_DATA_UNAVAILABLE


def test_mcp_read_failure_can_switch_to_complete_export_without_mixing(tmp_path):
    fake = FakeProvider()
    fake._mcp_client = object()
    fake.export_dir = tmp_path
    fake.fetch = FetchResult(
        "fake",
        "g1@chatroom",
        [],
        ProviderStatus.READ_FAILED,
        "MCP timeout",
    )
    export_message = _raw(source="export", source_message_id="export-1")
    fake._fetch_messages_export = lambda group_id, start, end: FetchResult(
        "fake",
        group_id,
        [export_message],
        ProviderStatus.OK,
        "export ok",
    )
    source = WeChatDataAnalysisSource(
        settings=Settings(
            _env_file=None,
            wechat_runtime_export_fallback_enabled=True,
        ),
        provider=fake,
    )

    result = source.fetch_messages(
        "g1@chatroom",
        datetime(2026, 8, 17),
        datetime(2026, 8, 18),
    )

    assert result.status == DataSourceStatus.OK
    assert [message.message_id for message in result.messages] == ["export-1"]
    assert result.meta["fallback_used"] is True
    assert result.meta["provider_chain"] == [
        "wechat_data_analysis_mcp",
        "wechat_data_analysis_export",
    ]
    assert result.meta["primary_error"] == "MCP timeout"


def test_mcp_read_failure_accepts_complete_empty_export_as_authoritative(tmp_path):
    fake = FakeProvider()
    fake._mcp_client = object()
    fake.export_dir = tmp_path
    fake.fetch = FetchResult(
        "fake",
        "g1@chatroom",
        [],
        ProviderStatus.READ_FAILED,
        "MCP timeout",
    )
    fake._fetch_messages_export = lambda group_id, start, end: FetchResult(
        "fake",
        group_id,
        [],
        ProviderStatus.EMPTY_RESULT,
        "export complete but empty",
    )
    source = WeChatDataAnalysisSource(
        settings=Settings(
            _env_file=None,
            wechat_runtime_export_fallback_enabled=True,
        ),
        provider=fake,
    )

    result = source.fetch_messages(
        "g1@chatroom",
        datetime(2026, 8, 17),
        datetime(2026, 8, 18),
    )

    assert result.status == DataSourceStatus.EMPTY_RESULT
    assert result.messages == []
    assert result.meta["fallback_used"] is True
    assert result.meta["provider_chain"] == [
        "wechat_data_analysis_mcp",
        "wechat_data_analysis_export",
    ]
