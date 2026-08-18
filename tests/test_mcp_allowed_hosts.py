"""Docker 化：MCP 主机放行单元测试。

默认仅允许本机回环；配置 wechat_mcp_allowed_hosts 后才允许额外主机
（如 Docker 的 host.docker.internal）。
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.providers.history import wechat_data_analysis
from app.providers.history.wechat_mcp import MCPClient, MCPConfigError, build_mcp_client


def test_default_rejects_non_loopback():
    with pytest.raises(MCPConfigError) as e:
        MCPClient("http://host.docker.internal:10392/mcp", "tok", 10)
    assert "回环" in str(e.value)


def test_loopback_allowed_by_default():
    client = MCPClient("http://127.0.0.1:10392/mcp", "tok", 10)
    assert client.url == "http://127.0.0.1:10392/mcp"


def test_allowed_hosts_accepts_extra():
    client = MCPClient(
        "http://host.docker.internal:10392/mcp",
        "tok",
        10,
        allowed_hosts=frozenset({"host.docker.internal"}),
    )
    assert client.url.startswith("http://host.docker.internal")


def test_build_mcp_client_passes_allowed_hosts():
    client = build_mcp_client(
        "http://host.docker.internal:10392/mcp",
        "tok",
        10,
        allowed_hosts=frozenset({"host.docker.internal"}),
    )
    assert client is not None


def test_build_mcp_client_still_rejects_remote_by_default():
    with pytest.raises(MCPConfigError):
        build_mcp_client("http://192.168.1.10:10392/mcp", "tok", 10)


def test_parse_allowed_hosts():
    assert wechat_data_analysis._parse_allowed_hosts("") is None
    assert wechat_data_analysis._parse_allowed_hosts("host.docker.internal") == frozenset(
        {"host.docker.internal"}
    )
    assert wechat_data_analysis._parse_allowed_hosts(
        " HostA , host.docker.internal "
    ) == frozenset({"hosta", "host.docker.internal"})


def test_settings_field_exists():
    s = Settings(_env_file=None)
    assert s.wechat_mcp_allowed_hosts == ""
