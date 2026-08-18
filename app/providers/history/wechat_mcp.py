"""WeChatDataAnalysis 本地 MCP 客户端（仅允许本机回环连接）。

实现极小化的 JSON-RPC / MCP 调用器：
- 仅接受 loopback HTTP(S) 地址（127.0.0.1 / localhost / ::1）；
- 每次请求携带 `Authorization: Bearer <token>`；
- 发送 `tools/call` 并返回 `result.structuredContent`；
- 将畸形响应、HTTP 错误、无效令牌与超时转换为不含令牌的 MCPError。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# 默认回环主机集合（可传入额外允许主机，如 Docker 的 host.docker.internal）
DEFAULT_ALLOWED_HOSTS = frozenset()

# 本机回环连接必须绕过系统/环境代理（代理会把本地请求转发出去导致 502）。
_PROXY_FREE_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class MCPError(Exception):
    """MCP 调用失败（错误信息不包含令牌）。"""


class MCPConfigError(MCPError):
    """MCP 配置不合法（非回环地址 / 缺少令牌）。"""


class MCPClient:
    """JSON-RPC 2.0 + MCP `tools/call` 调用器。"""

    def __init__(
        self,
        url: str,
        token: str,
        timeout: float = 10.0,
        allowed_hosts: frozenset | None = None,
    ) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        # 额外允许的主机（如 Docker 的 host.docker.internal）；默认仅回环
        self._allowed_hosts = frozenset(allowed_hosts or DEFAULT_ALLOWED_HOSTS)
        self._validate()

    def _validate(self) -> None:
        parts = urlsplit(self.url)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise MCPConfigError("wechat_mcp_url 仅支持 http/https 协议")
        host = (parts.hostname or "").lower()
        allowed = LOOPBACK_HOSTS | self._allowed_hosts
        if host not in allowed:
            raise MCPConfigError(
                "wechat_mcp_url 仅允许本机回环地址（127.0.0.1 / localhost / ::1）"
                + (f" 或配置的额外主机（{sorted(self._allowed_hosts)}）" if self._allowed_hosts else "")
                + "，已拒绝远程连接"
            )
        if not self.token.strip():
            raise MCPConfigError(
                "已配置 wechat_mcp_url 但缺少 wechat_mcp_token，请填写本地服务令牌"
            )
        if not (0 < float(self.timeout) <= 120):
            raise MCPConfigError("wechat_mcp_timeout_seconds 必须是 1~120 之间的数字")

    def call(self, method: str, params: dict) -> dict:
        """调用指定 MCP 工具，返回 structuredContent（dict）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": method, "arguments": params},
        }
        request = urllib.request.Request(
            self.url,
            # ensure_ascii=False：中文原样发送。WeChatDataAnalysis 服务端
            # 不解析 \uXXXX 转义，转义后的中文查询词会匹配不到任何会话。
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with _PROXY_FREE_OPENER.open(request, timeout=float(self.timeout)) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise MCPError("本地服务认证失败（令牌无效或未授权）") from e
            raise MCPError(f"本地服务 HTTP 错误 {e.code}") from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            raise MCPError(f"无法连接本地服务：{reason}") from e
        except (socket.timeout, TimeoutError, OSError) as e:
            raise MCPError(f"本地服务请求超时：{e}") from e

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise MCPError("本地服务响应不是有效 JSON") from e
        if not isinstance(data, dict):
            raise MCPError("本地服务响应格式错误")

        if data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                code = err.get("code", "")
                message = err.get("message", "")
                raise MCPError(f"本地服务返回错误：{message}（code={code}）")
            raise MCPError(f"本地服务返回错误：{err}")

        result = data.get("result")
        if not isinstance(result, dict):
            raise MCPError("本地服务响应缺少 result")
        if result.get("isError"):
            message = _result_error_message(result)
            raise MCPError(f"本地服务调用失败：{message}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise MCPError("本地服务响应缺少 structuredContent")
        return structured


def _result_error_message(result: dict) -> str:
    content = result.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                return str(item["text"])[:120]
    return "未知错误"


def build_mcp_client(
    url: str,
    token: str,
    timeout: float,
    allowed_hosts: frozenset | None = None,
) -> MCPClient | None:
    """按配置构造 MCP 客户端。

    URL 与令牌都为空时返回 None（回退 JSON 导出模式）。只有 URL 或只有令牌
    同样视为未配置。非允许地址 / 非法超时抛出 MCPConfigError（Provider 会标记不可用）。
    allowed_hosts 供 Docker 容器访问宿主机场景（如 host.docker.internal）使用。
    """
    url = (url or "").strip()
    token = (token or "").strip()
    if not url or not token:
        return None
    return MCPClient(url=url, token=token, timeout=timeout, allowed_hosts=allowed_hosts)