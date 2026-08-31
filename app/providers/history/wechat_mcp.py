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
from threading import Event, Timer
from time import monotonic
from urllib.parse import urlsplit

from app.core.logging import get_logger

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# 默认回环主机集合（可传入额外允许主机，如 Docker 的 host.docker.internal）
DEFAULT_ALLOWED_HOSTS = frozenset()

# 本机回环连接必须绕过系统/环境代理（代理会把本地请求转发出去导致 502）。
_PROXY_FREE_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_READ_CHUNK_SIZE = 64 * 1024
_MAX_RESPONSE_BYTES = 128 * 1024 * 1024
logger = get_logger("groupbrief.wechat_mcp")


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

    def call(self, method: str, params: dict, *, timeout: float | None = None) -> dict:
        """调用指定 MCP 工具，返回 structuredContent（dict）。"""
        request_timeout = float(self.timeout if timeout is None else timeout)
        if not (0 < request_timeout <= 120):
            raise MCPConfigError("MCP 单次请求超时必须是 1~120 之间的数字")
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
        deadline = monotonic() + request_timeout
        try:
            with _PROXY_FREE_OPENER.open(request, timeout=request_timeout) as resp:
                expired = Event()
                deadline_timer = _start_response_deadline_timer(resp, deadline, expired)
                try:
                    body_bytes = _read_response_body(resp, deadline)
                finally:
                    deadline_timer.cancel()
                if expired.is_set():
                    raise TimeoutError("超过整次请求截止时间")
                body = body_bytes.decode("utf-8", errors="replace")
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


def _read_response_body(response, deadline: float) -> bytes:
    """分块读取响应并执行整次请求硬截止，避免持续输出绕过 socket 超时。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("超过整次请求截止时间")
        _set_response_socket_timeout(response, remaining)
        try:
            chunk = response.read(_READ_CHUNK_SIZE)
            sized_read = True
        except TypeError:
            # 兼容测试假响应及少数只提供 read() 的 file-like 对象。
            chunk = response.read()
            sized_read = False
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise MCPError("本地服务响应过大，已停止读取")
        if monotonic() >= deadline:
            raise TimeoutError("超过整次请求截止时间")
        if not sized_read:
            break
    return b"".join(chunks)


def _start_response_deadline_timer(response, deadline: float, expired: Event) -> Timer:
    """到达总截止时间时主动中断连接，防止小数据流不断刷新 socket 超时。"""
    remaining = max(deadline - monotonic(), 0.001)

    def _abort() -> None:
        expired.set()
        response_socket = _response_socket(response)
        if response_socket is not None:
            try:
                response_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.debug("MCP 截止计时器关闭响应 socket 失败", exc_info=True)
            try:
                response_socket.close()
            except OSError:
                logger.debug("MCP 截止计时器释放响应 socket 失败", exc_info=True)
            return
        try:
            response.close()
        except (AttributeError, OSError):
            logger.debug("MCP 截止计时器关闭响应失败", exc_info=True)

    timer = Timer(remaining, _abort)
    timer.daemon = True
    timer.start()
    return timer


def _response_socket(response):
    """获取 urllib/http.client 响应的底层 socket；假响应允许返回 None。"""
    try:
        return response.fp.raw._sock
    except AttributeError:
        return None


def _set_response_socket_timeout(response, remaining: float) -> None:
    """尽力把底层 socket 超时收紧为剩余总时限；假响应不要求该属性。"""
    response_socket = _response_socket(response)
    if response_socket is not None:
        try:
            response_socket.settimeout(max(remaining, 0.001))
        except OSError:
            logger.debug("MCP 响应 socket 超时设置失败", exc_info=True)


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
