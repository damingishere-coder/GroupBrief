"""P7 测试：WeChatDataAnalysis MCP 直连（全部使用假客户端/注入，不依赖真实服务）。

覆盖：loopback 校验、token 掩码、MCP 健康检查、群候选过滤、精确/模糊群名解析、
分页与时间窗消息过滤、错误处理、未配置时的导出回退，以及解析路径绝不混入 Mock。
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.testclient import TestClient

from app.api import settings as settings_api
from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.providers.history.base import ProviderStatus
from app.providers.history.mock import MockProvider
from app.providers.history.wechat_data_analysis import (
    WeChatDataAnalysisProvider,
    _mcp_timestamp,
    _sanitize_sender_name,
)
from app.providers.history.wechat_mcp import (
    MCPClient,
    MCPConfigError,
    MCPError,
    build_mcp_client,
)
from app.services.history_service import HistoryService

WINDOW_START = datetime(2026, 8, 10, 0, 0, 0)
WINDOW_END = datetime(2026, 8, 17, 23, 59, 59)


# ---------- 假 MCP 客户端 ----------


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.handlers: dict[str, callable] = {}

    def on(self, method: str, handler) -> "FakeMCPClient":
        self.handlers[method] = handler
        return self

    def call(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        handler = self.handlers.get(method)
        if handler is None:
            raise MCPError(f"无处理器：{method}")
        return handler(params)


def raise_mcp(message: str):
    def _handler(params: dict) -> dict:
        raise MCPError(message)

    return _handler


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False


def _provider(fake: FakeMCPClient) -> WeChatDataAnalysisProvider:
    return WeChatDataAnalysisProvider(mcp_client=fake, settings=Settings())


def _healthy_fake() -> FakeMCPClient:
    return FakeMCPClient().on("wechat.core.get_status", lambda params: {"status": "ok"})


def _msg(
    msg_id: str,
    ts: float,
    sender: str = "wxid_user",
    name: str = "成员",
    content: str = "内容",
    render: int = 1,
) -> dict:
    return {
        "id": msg_id,
        "createTime": ts,
        "senderUsername": sender,
        "senderDisplayName": name,
        "content": content,
        "renderType": render,
    }


# ---------- 回环地址校验 ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:10392/mcp",
        "https://localhost:8443/mcp",
        "http://[::1]:10392/mcp",
    ],
)
def test_loopback_urls_accepted(url: str):
    client = MCPClient(url, "token-x", timeout=10)
    assert client.url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "http://192.168.1.10:10392/mcp",
        "http://10.0.0.1:1/mcp",
        "https://wechat.example.org/mcp",
    ],
)
def test_non_loopback_urls_rejected(url: str):
    with pytest.raises(MCPConfigError):
        MCPClient(url, "token-x", timeout=10)


def test_bad_scheme_rejected():
    with pytest.raises(MCPConfigError):
        MCPClient("ftp://127.0.0.1/mcp", "token-x")


def test_missing_token_rejected():
    with pytest.raises(MCPConfigError):
        MCPClient("http://127.0.0.1:10392/mcp", "   ")


def test_invalid_timeout_rejected():
    with pytest.raises(MCPConfigError):
        MCPClient("http://127.0.0.1:10392/mcp", "token-x", timeout=0)
    with pytest.raises(MCPConfigError):
        MCPClient("http://127.0.0.1:10392/mcp", "token-x", timeout=999)


def test_build_unconfigured_returns_none():
    assert build_mcp_client("", "", 10) is None
    assert build_mcp_client("http://127.0.0.1:10392/mcp", "", 10) is None
    assert build_mcp_client("", "token", 10) is None


def test_build_non_loopback_raises():
    with pytest.raises(MCPConfigError):
        build_mcp_client("http://remote.example/mcp", "token", 10)


# ---------- JSON-RPC 调用与错误处理 ----------


def test_mcp_client_sends_auth_and_parses(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["auth"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"status": "ok", "running": True}}}
        ).encode("utf-8")
        return FakeResponse(body)

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "secret-token-1", timeout=10)
    result = client.call("wechat.core.get_status", {})
    assert result == {"status": "ok", "running": True}
    assert captured["auth"] == "Bearer secret-token-1"
    assert captured["payload"]["method"] == "tools/call"
    assert captured["payload"]["params"]["name"] == "wechat.core.get_status"


def test_mcp_client_http_401_no_token_leak(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError("http://127.0.0.1/mcp", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "secret-token-1")
    with pytest.raises(MCPError) as exc:
        client.call("wechat.core.get_status", {})
    assert "认证" in str(exc.value)
    assert "secret-token-1" not in str(exc.value)


def test_mcp_client_http_500(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError("http://127.0.0.1/mcp", 500, "Server Error", {}, None)

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "secret-token-1")
    with pytest.raises(MCPError) as exc:
        client.call("wechat.core.get_status", {})
    assert "500" in str(exc.value)


def test_mcp_client_malformed_json(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return FakeResponse(b"not-json")

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "token")
    with pytest.raises(MCPError):
        client.call("wechat.core.get_status", {})


def test_mcp_client_missing_structured_content(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return FakeResponse(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}).encode())

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "token")
    with pytest.raises(MCPError):
        client.call("wechat.core.get_status", {})


def test_mcp_client_is_error_result(monkeypatch):
    def fake_urlopen(request, timeout=None):
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"isError": True, "content": [{"type": "text", "text": "tool exploded"}]},
            }
        ).encode()
        return FakeResponse(body)

    monkeypatch.setattr("app.providers.history.wechat_mcp._PROXY_FREE_OPENER.open", fake_urlopen)
    client = MCPClient("http://127.0.0.1:10392/mcp", "token")
    with pytest.raises(MCPError) as exc:
        client.call("wechat.chat.get_messages", {})
    assert "tool exploded" in str(exc.value)


# ---------- 健康检查 ----------


def test_mcp_health_ok():
    fake = FakeMCPClient().on("wechat.core.get_status", lambda params: {"status": "ok"})
    health = _provider(fake).health_check()
    assert health.ok
    assert health.status == ProviderStatus.OK
    assert "WeChatDataAnalysis" in health.detail


def test_mcp_health_auth_failure():
    fake = FakeMCPClient().on("wechat.core.get_status", raise_mcp("本地服务认证失败（令牌无效或未授权）"))
    health = _provider(fake).health_check()
    assert not health.ok
    assert health.status == ProviderStatus.UNAVAILABLE
    assert "wechat_mcp_token" in health.detail


def test_mcp_health_unreachable():
    fake = FakeMCPClient().on("wechat.core.get_status", raise_mcp("无法连接本地服务：连接被拒绝"))
    health = _provider(fake).health_check()
    assert not health.ok
    assert "启动本地 WeChatDataAnalysis 服务" in health.detail


def test_non_loopback_provider_unavailable():
    provider = WeChatDataAnalysisProvider(
        export_dir="C:/does/not/exist/xyz",
        settings=Settings(wechat_mcp_url="http://remote.example/mcp", wechat_mcp_token="tok"),
    )
    health = provider.health_check()
    assert not health.ok
    assert health.status == ProviderStatus.UNAVAILABLE
    assert "回环" in health.detail or "远程" in health.detail or "拒绝" in health.detail


# ---------- 群名解析与群候选过滤 ----------


def test_resolve_groups_only_chatrooms():
    fake = FakeMCPClient().on(
        "wechat.chat.resolve_session",
        lambda params: {
            "sessions": [
                {"username": "wxid_abc123", "name": "个人联系人", "type": "friend"},
                {"username": "group1@chatroom", "name": "产品经理交流群", "memberCount": 120, "type": "chatroom"},
                {"username": "group2@chatroom", "displayName": "产品经理茶话会", "member_count": 88},
                {"username": "g3@chatroom", "remark": "技术群", "type": "GROUP"},
            ]
        },
    )
    groups = _provider(fake).resolve_groups("产品经理")
    assert [g.group_id for g in groups] == ["group1@chatroom", "group2@chatroom", "g3@chatroom"]
    assert all(g.group_id.endswith("@chatroom") for g in groups)
    assert groups[0].group_name == "产品经理交流群"
    assert groups[0].member_count == 120
    method, params = fake.calls[0]
    assert method == "wechat.chat.resolve_session"
    assert params["query"] == "产品经理"
    assert params["source"] == "auto"


def test_resolve_exact_via_service():
    fake = _healthy_fake().on(
        "wechat.chat.resolve_session",
        lambda params: {"sessions": [{"username": "real-1@chatroom", "name": "产品经理交流群", "memberCount": 10}]},
    )
    service = HistoryService()
    service.providers = [_provider(fake)]
    matches = service.resolve_group_names("产品经理交流群")
    assert len(matches) == 1
    assert matches[0].match_type == "exact"
    assert matches[0].group_id == "real-1@chatroom"
    assert matches[0].provider == "wechat_data_analysis"


def test_resolve_partial_via_service():
    fake = _healthy_fake().on(
        "wechat.chat.resolve_session",
        lambda params: {"sessions": [{"username": "real-1@chatroom", "name": "产品经理交流群"}]},
    )
    service = HistoryService()
    service.providers = [_provider(fake)]
    matches = service.resolve_group_names("产品经理")
    assert [m.group_id for m in matches] == ["real-1@chatroom"]
    assert matches[0].match_type == "partial"


def test_resolve_never_mixes_mock():
    fake = _healthy_fake().on(
        "wechat.chat.resolve_session",
        lambda params: {"sessions": [{"username": "real-1@chatroom", "name": "产品经理交流群"}]},
    )
    service = HistoryService()
    service.providers = [_provider(fake), MockProvider()]
    matches = service.resolve_group_names("产品经理交流群")
    assert [m.group_id for m in matches] == ["real-1@chatroom"]
    assert all(m.provider == "wechat_data_analysis" for m in matches)


def test_list_groups_mcp_uses_list_sessions():
    fake = FakeMCPClient().on(
        "wechat.chat.list_sessions",
        lambda params: {"sessions": [{"username": "g@chatroom", "name": "群", "memberCount": 3}]},
    )
    groups = _provider(fake).list_groups()
    assert [g.group_id for g in groups] == ["g@chatroom"]
    assert fake.calls[0][0] == "wechat.chat.list_sessions"
    assert "query" not in fake.calls[0][1]


# ---------- 消息读取：锚点、时间窗、转换 ----------


def test_range_read_one_call_and_preserves_group_display_name():
    fake = FakeMCPClient().on(
        "wechat.chat.get_messages_range",
        lambda params: {
            "messages": [
                _msg("m1", 1786420000, "lxh327625169com", "🌸林诗雅小仙女", "内容", 1),
                _msg("m2", 1786420100, "HIPHOPLZG", "请下载“生气”App", "内容", 1),
            ],
            "hasMore": False,
            "nextOffset": None,
            "readStrategy": "realtime_keyset",
            "serverElapsedMs": 321,
        },
    )
    result = _provider(fake).fetch_messages("group@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.OK
    assert [item.sender_name for item in result.messages] == ["🌸林诗雅小仙女", "请下载“生气”App"]
    assert [method for method, _ in fake.calls] == ["wechat.chat.get_messages_range"]
    assert fake.calls[0][1]["limit"] == 2000
    assert result.meta["mcp_call_count"] == 1
    assert result.meta["read_strategy"] == "realtime_keyset"
    assert result.meta["server_elapsed_ms"] == 321


def test_range_read_pages_until_has_more_false_and_deduplicates():
    def handler(params: dict) -> dict:
        if params["offset"] == 0:
            return {
                "messages": [_msg("m1", 1786420000), _msg("dup", 1786420001)],
                "hasMore": True,
                "nextOffset": 2,
            }
        return {
            "messages": [_msg("dup", 1786420001), _msg("m2", 1786420002)],
            "hasMore": False,
            "nextOffset": None,
        }

    fake = FakeMCPClient().on("wechat.chat.get_messages_range", handler)
    result = _provider(fake).fetch_messages("group@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.OK
    assert [item.source_message_id for item in result.messages] == ["m1", "dup", "m2"]
    assert [params["offset"] for _, params in fake.calls] == [0, 2]
    assert result.meta["range_page_count"] == 2


def test_range_read_failure_does_not_silently_fallback():
    fake = FakeMCPClient().on(
        "wechat.chat.get_messages_range",
        raise_mcp("本地服务请求超时"),
    ).on("wechat.chat.get_message_anchor", lambda params: {"anchorId": None})
    result = _provider(fake).fetch_messages("group@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.READ_FAILED
    assert [method for method, _ in fake.calls] == ["wechat.chat.get_messages_range"]


def test_invisible_nickname_sanitizer_preserves_chinese_and_emoji():
    assert _sanitize_sender_name("\u3164\u3164笑我\u200b") == "笑我"
    assert _sanitize_sender_name("🌸林诗雅小仙女") == "🌸林诗雅小仙女"

# 注意：_fetch_messages_mcp 现在用 get_message_anchor（定位某天第一条）
# + get_message_around（从锚点向两端翻页），不再用 get_messages。
# 测试里 around 返回的消息列表含窗口外首尾（保证两个方向各一轮即停）。


def _anchor_around_fake(messages: list[dict]) -> FakeMCPClient:
    """构造模拟锚点接口的假客户端：任意日期都返回同一锚点与同一批消息。"""
    fake = FakeMCPClient()
    fake.on(
        "wechat.chat.get_message_anchor",
        lambda params: {"anchorId": "anchor-0", "createTime": messages[0]["createTime"]},
    )
    fake.on(
        "wechat.chat.get_message_around",
        lambda params: {"messages": messages},
    )
    return fake


def test_fetch_messages_paging_and_conversion():
    # 窗口 2026-08-10 ~ 2026-08-17；首尾各放一条窗口外消息保证翻页一轮即停
    msgs = (
        [_msg("head", 1786000000, "u", "n", "窗口外旧消息", 1)]  # 早于窗口起点
        + [_msg(f"m{i}", 1786410000 + i, f"u{i}", f"n{i}", f"c{i}", 1) for i in range(100)]
        + [_msg("tail", 1787200000, "u", "n", "窗口外新消息", 1)]  # 晚于窗口终点
    )
    fake = _anchor_around_fake(msgs)
    result = _provider(fake).fetch_messages("group@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.OK
    assert len(result.messages) == 100
    calls = [m for m, _ in fake.calls]
    assert "wechat.chat.get_message_anchor" in calls
    assert "wechat.chat.get_message_around" in calls
    assert "wechat.chat.get_messages" not in calls
    first = result.messages[0]
    assert first.group_id == "group@chatroom"
    assert first.source == "wechat_data_analysis"
    assert first.source_message_id == "m0"
    assert first.sender_id == "u0"


def test_fetch_messages_multiple_days_dedup():
    """多天窗口（如周一汇总周六+周日）取多个锚点，重复消息去重。"""
    anchor_params: list[dict] = []

    def anchor_handler(params: dict) -> dict:
        anchor_params.append(params)
        return {"anchorId": "anchor-0", "createTime": 1786410000}

    fake = FakeMCPClient()
    fake.on("wechat.chat.get_message_anchor", anchor_handler)
    fake.on(
        "wechat.chat.get_message_around",
        lambda params: {
            "messages": [
                _msg("head", 1786000000, "u", "n", "窗口外旧消息", 1),
                _msg("dup", 1786420000, "u", "n", "同一消息", 1),
                _msg("tail", 1787200000, "u", "n", "窗口外", 1),
            ]
        },
    )
    result = _provider(fake).fetch_messages("g@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.OK
    assert len(result.messages) == 1
    assert result.messages[0].source_message_id == "dup"
    # 窗口内每天都会请求锚点
    assert len(anchor_params) >= 2


def test_fetch_messages_date_filtering_and_conversion():
    fake = _anchor_around_fake(
        [
            _msg("old", 1786280000, "wxid_a", "张三", "窗口外旧消息", 1),  # 2026-08-09T13:53+08
            _msg("in", 1786420000, "wxid_b", "李四", "窗口内消息", 1),
            _msg("future", 1787200000, "wxid_c", "王五", "窗口外新消息", 1),
            _msg("image", 1786421000, "wxid_d", "赵六", "", 2),
        ]
    )
    result = _provider(fake).fetch_messages("g@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.OK
    assert len(result.messages) == 2
    ids = {m.source_message_id for m in result.messages}
    assert ids == {"in", "image"}
    image = next(m for m in result.messages if m.source_message_id == "image")
    assert image.message_type == "image"
    assert image.sender_id == "wxid_d"
    assert image.sender_name == "赵六"
    text = next(m for m in result.messages if m.source_message_id == "in")
    assert text.message_type == "text"
    assert text.content == "窗口内消息"


def test_fetch_messages_anchor_missing_empty_result():
    """锚点不存在（当天无消息）→ 空结果。"""
    fake = FakeMCPClient().on(
        "wechat.chat.get_message_anchor",
        lambda params: {"status": "success", "anchorId": None},
    )
    result = _provider(fake).fetch_messages("g@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.EMPTY_RESULT


def test_fetch_messages_read_failed():
    fake = FakeMCPClient().on("wechat.chat.get_message_anchor", raise_mcp("本地服务 HTTP 错误 500"))
    result = _provider(fake).fetch_messages("g@chatroom", WINDOW_START, WINDOW_END)
    assert result.status == ProviderStatus.READ_FAILED
    assert "500" in result.detail


# ---------- 时间戳解析 ----------


def test_timestamp_milliseconds():
    ts = _mcp_timestamp(1786420000000)  # 毫秒 = 2026-08-11T11:46:40+08:00
    assert ts is not None
    assert (ts.year, ts.month, ts.day) == (2026, 8, 11)


def test_timestamp_iso_string():
    ts = _mcp_timestamp("2026-08-10T11:46:40+08:00")
    assert ts == datetime(2026, 8, 10, 11, 46, 40)


def test_timestamp_invalid_returns_none():
    assert _mcp_timestamp(None) is None
    assert _mcp_timestamp("") is None
    assert _mcp_timestamp("not-a-date") is None


# ---------- 未配置时的导出回退 ----------


def test_export_fallback_when_mcp_unconfigured():
    provider = WeChatDataAnalysisProvider(
        export_dir="C:/does/not/exist/xyz",
        settings=Settings(wechat_mcp_url="", wechat_mcp_token=""),
    )
    assert provider._mcp_client is None
    health = provider.health_check()
    assert not health.ok
    assert "MCP" in health.detail or "导出" in health.detail or "wechat_mcp" in health.detail


# ---------- 运行期设置应用 ----------


def test_apply_runtime_values_types():
    settings = Settings()
    applied = settings.apply_runtime_values(
        {
            "history_provider_mock_enabled": "false",
            "wechat_mcp_timeout_seconds": "25",
            "wechat_mcp_account": "acc-1",
            "ai_timeout_seconds": "42",
        }
    )
    assert settings.history_provider_mock_enabled is False
    assert settings.wechat_mcp_timeout_seconds == 25
    assert settings.wechat_mcp_account == "acc-1"
    assert settings.ai_timeout_seconds == 42
    assert set(applied) == {
        "history_provider_mock_enabled",
        "wechat_mcp_timeout_seconds",
        "wechat_mcp_account",
        "ai_timeout_seconds",
    }


def test_apply_runtime_values_ignores_mask_and_unknown():
    settings = Settings()
    settings.wechat_mcp_token = "existing-token"
    applied = settings.apply_runtime_values({"wechat_mcp_token": "******", "not_a_key": "x"})
    assert settings.wechat_mcp_token == "existing-token"
    assert applied == []


# ---------- 设置 API：token 掩码与运行期生效 ----------


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repo.engine = engine

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(settings_api.router)
    app.dependency_overrides[repo.get_session] = override_session

    with TestClient(app) as test_client:
        yield test_client, engine


def test_settings_get_masks_mcp_token(client):
    test_client, engine = client
    with Session(engine) as session:
        repo.set_setting_value(session, "wechat_mcp_token", "super-secret-token")
    data = test_client.get("/api/settings").json()
    assert data["wechat_mcp_token"] == "******"


def test_settings_put_masked_does_not_overwrite(client):
    test_client, engine = client
    with Session(engine) as session:
        repo.set_setting_value(session, "wechat_mcp_token", "real-token")
    resp = test_client.put("/api/settings", json={"values": {"wechat_mcp_token": "******"}})
    assert resp.status_code == 200
    with Session(engine) as session:
        assert repo.get_setting_value(session, "wechat_mcp_token") == "real-token"


def test_settings_put_empty_token_does_not_clear(client):
    test_client, engine = client
    with Session(engine) as session:
        repo.set_setting_value(session, "wechat_mcp_token", "real-token")
    resp = test_client.put("/api/settings", json={"values": {"wechat_mcp_token": ""}})
    assert resp.status_code == 200
    with Session(engine) as session:
        assert repo.get_setting_value(session, "wechat_mcp_token") == "real-token"


def test_settings_put_applies_to_runtime(client):
    test_client, _ = client
    settings = get_settings()
    original = settings.wechat_mcp_account
    try:
        resp = test_client.put("/api/settings", json={"values": {"wechat_mcp_account": "runtime-acc-9"}})
        assert resp.status_code == 200
        assert get_settings().wechat_mcp_account == "runtime-acc-9"
    finally:
        settings.wechat_mcp_account = original


def test_settings_put_applies_new_token_to_runtime(client):
    test_client, _ = client
    settings = get_settings()
    original_token = settings.wechat_mcp_token
    original_url = settings.wechat_mcp_url
    try:
        resp = test_client.put(
            "/api/settings",
            json={"values": {"wechat_mcp_token": "brand-new-token", "wechat_mcp_url": "http://127.0.0.1:10392/mcp"}},
        )
        assert resp.status_code == 200
        assert get_settings().wechat_mcp_token == "brand-new-token"
    finally:
        settings.wechat_mcp_token = original_token
        settings.wechat_mcp_url = original_url
