"""WeChatDataAnalysis Provider（主读取路线）。

GroupBrief 不关心 WeChatDataAnalysis 内部如何解析微信数据库。
数据来源有两种，MCP 优先：

1. 本地 MCP 服务：当配置了回环地址 + 令牌后，直接调用 WeChatDataAnalysis
   本地服务的 JSON-RPC MCP 接口（health / resolve_session / get_messages）。
2. 结构化 JSON 导出：WeChatDataAnalysis 导出的 groups.json + messages/ 目录
   （向后兼容的备用来源）。

当真实数据不可用时返回明确状态（UNAVAILABLE / UNSUPPORTED_WECHAT_VERSION），
并提示如何启用本地服务或配置导出，由上层自动降级到 wechat-cli 或 Mock。
令牌、聊天内容与用户路径不会写入日志或返回给接口。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.providers.history.base import (
    ChatHistoryProvider,
    FetchResult,
    GroupInfo,
    ProviderHealth,
    ProviderStatus,
    RawMessage,
)
from app.providers.history.wechat_mcp import (
    MCPClient,
    MCPConfigError,
    MCPError,
    build_mcp_client,
)

logger = get_logger("groupbrief.providers")

DEFAULT_WECHAT_DIRS = [
    Path(os.environ.get("USERPROFILE", "")) / "Documents" / "WeChat Files",
    Path(os.environ.get("USERPROFILE", "")) / "Documents" / "xwechat_files",
]

_MCP_PAGE_SIZE = 100
_MCP_MAX_PAGES = 1000

_CANDIDATE_LIST_KEYS = ("sessions", "candidates", "results", "items", "list")
_ITEM_LIST_KEYS = ("messages", "items", "list", "results", "records")

_RENDER_TYPE_MAP = {
    1: "text",
    2: "image",
    3: "voice",
    4: "video",
    5: "file",
    6: "location",
    7: "system",
    8: "link",
    9: "emoji",
    10: "quote",
    34: "voice",
    49: "file",
    10002: "system",
}


def _load_export_file(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class WeChatDataAnalysisProvider(ChatHistoryProvider):
    name = "wechat_data_analysis"

    def __init__(
        self,
        export_dir: Path | str | None = None,
        mcp_client: MCPClient | None = None,
        settings: Settings | None = None,
    ):
        settings = settings or get_settings()
        self._settings = settings
        if export_dir:
            self.export_dir = Path(export_dir)
        elif settings.wechat_export_dir:
            self.export_dir = Path(settings.wechat_export_dir)
        else:
            self.export_dir = settings.data_dir / "wechat_export"
        self._groups_cache: list[GroupInfo] | None = None
        self.wechat_mcp_account = settings.wechat_mcp_account
        self._mcp_config_error: str | None = None
        if mcp_client is not None:
            # 测试注入的假客户端直接使用；真实路径通过 build_mcp_client 构造。
            self._mcp_client = mcp_client
        else:
            try:
                self._mcp_client = build_mcp_client(
                    settings.wechat_mcp_url,
                    settings.wechat_mcp_token,
                    settings.wechat_mcp_timeout_seconds,
                )
            except MCPConfigError as e:
                self._mcp_client = None
                self._mcp_config_error = str(e)

    # ---------- 健康检查 ----------

    def health_check(self) -> ProviderHealth:
        if self._mcp_client is not None:
            return self._mcp_health()
        if self._mcp_config_error:
            return ProviderHealth(
                self.name,
                ProviderStatus.UNAVAILABLE,
                f"{self._mcp_config_error}。或清空 MCP 配置并配置 WECHAT_EXPORT_DIR 使用 JSON 导出。",
            )
        # JSON 导出后备来源
        if self.export_dir.exists():
            group_file = self.export_dir / "groups.json"
            if group_file.exists():
                return ProviderHealth(self.name, ProviderStatus.OK, f"导出数据就绪：{self.export_dir}")
            return ProviderHealth(
                self.name,
                ProviderStatus.EMPTY_RESULT,
                f"导出目录存在但缺少 groups.json：{self.export_dir}",
            )
        wechat_dir = self._find_wechat_dir()
        if wechat_dir:
            return ProviderHealth(
                self.name,
                ProviderStatus.UNSUPPORTED_WECHAT_VERSION,
                f"已发现微信数据目录 {wechat_dir}，但未配置可用的数据源。请任选其一："
                "① 在设置中填写本地 WeChatDataAnalysis MCP 令牌（wechat_mcp_token）启用直连；"
                f"② 用 WeChatDataAnalysis 导出群聊 JSON 到 {self.export_dir}（含 groups.json 与 messages/ 目录）。",
            )
        return ProviderHealth(
            self.name,
            ProviderStatus.UNAVAILABLE,
            "未找到微信数据目录，也未配置数据源。请安装微信并登录，或在设置中配置本地 "
            "WeChatDataAnalysis MCP 令牌，或用 WeChatDataAnalysis 导出数据。",
        )

    def _mcp_health(self) -> ProviderHealth:
        try:
            status = self._mcp_client.call("wechat.core.get_status", {})
        except MCPError as e:
            message = str(e)
            if "认证" in message:
                detail = "本地 WeChatDataAnalysis 服务认证失败，请检查设置中的 wechat_mcp_token"
            else:
                detail = f"{message}。请启动本地 WeChatDataAnalysis 服务，或在设置中配置 JSON 导出目录。"
            return ProviderHealth(self.name, ProviderStatus.UNAVAILABLE, detail)
        state = _first_str(status, "status", "state", "message") or "running"
        return ProviderHealth(
            self.name,
            ProviderStatus.OK,
            f"本地 WeChatDataAnalysis 服务可用（{state[:80]}）",
        )

    def _find_wechat_dir(self) -> Path | None:
        settings = get_settings()
        if settings.wechat_data_dir:
            p = Path(settings.wechat_data_dir)
            if p.exists():
                return p
        for d in DEFAULT_WECHAT_DIRS:
            if d.exists() and any(d.iterdir()):
                return d
        return None

    # ---------- 群列表 ----------

    def list_groups(self) -> list[GroupInfo]:
        if self._mcp_client is not None:
            return self._list_groups_mcp()
        if self._groups_cache is not None:
            return self._groups_cache
        group_file = self.export_dir / "groups.json"
        if not group_file.exists():
            return []
        data = _load_export_file(group_file)
        groups = [
            GroupInfo(group_id=g["group_id"], group_name=g.get("group_name", ""), member_count=g.get("member_count", 0))
            for g in data
        ]
        self._groups_cache = groups
        return groups

    def _list_groups_mcp(self) -> list[GroupInfo]:
        # resolve_session 要求非空 query，全量列出会话改用 list_sessions
        params = {"limit": 100, "source": "auto"}
        if self.wechat_mcp_account:
            params["account"] = self.wechat_mcp_account
        try:
            structured = self._mcp_client.call("wechat.chat.list_sessions", params)
            return _parse_group_candidates(structured)
        except MCPError as e:
            logger.warning("MCP 群发现失败：%s", e)
            return []

    # ---------- 群名解析（直接解析，供 HistoryService 使用） ----------

    def resolve_groups(self, query: str) -> list[GroupInfo]:
        """按名称解析真实群（MCP 走 resolve_session；导出模式回落到全量群列表）。"""
        if self._mcp_client is not None:
            return self._resolve_groups_mcp(query)
        return list(self.list_groups())

    def _resolve_groups_mcp(self, query: str) -> list[GroupInfo]:
        params = {"query": query or "", "source": "auto", "limit": 20}
        if self.wechat_mcp_account:
            params["account"] = self.wechat_mcp_account
        try:
            structured = self._mcp_client.call("wechat.chat.resolve_session", params)
            return _parse_group_candidates(structured)
        except MCPError as e:
            logger.warning("MCP 群名解析失败：%s", e)
            return []

    # ---------- 消息读取 ----------

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        if self._mcp_client is not None:
            return self._fetch_messages_mcp(group_id, start_time, end_time)
        return self._fetch_messages_export(group_id, start_time, end_time)

    def _fetch_messages_export(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        # 导出数据按 群ID/日期.json 存放，与 fixtures 同构
        day_dir = self.export_dir / "messages" / _safe(group_id)
        if not day_dir.exists():
            return FetchResult(self.name, group_id, [], ProviderStatus.GROUP_NOT_FOUND, f"导出目录缺少 {group_id} 的消息数据")

        messages: list[RawMessage] = []
        day = start_time.date()
        last_day = end_time.date()
        while day <= last_day:
            file = day_dir / f"{day.isoformat()}.json"
            if file.exists():
                for item in _load_export_file(file):
                    ts = _normalize_ts(item["timestamp"])
                    if start_time <= ts <= end_time:
                        messages.append(_to_raw(item, ts))
            day = day.fromordinal(day.toordinal() + 1)

        if not messages:
            return FetchResult(self.name, group_id, [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
        return FetchResult(self.name, group_id, messages, ProviderStatus.OK)

    def _fetch_messages_mcp(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        """通过 MCP 读取群消息（锚点双向读取）。

        上游 get_messages 没有时间过滤参数、总是从最新消息返回，
        无法读取历史日期；改用锚点工具：
        - get_message_anchor(kind=day) 定位某天第一条消息；
        - get_message_around 以锚点为中心返回上下文（每轮约 50 条），
          用「上一条/下一条」作为新锚点向前后翻页；
        - 客户端按时间窗过滤，跨天消息靠去重避免重复。

        上游每次调用较慢（数秒），超时由 wechat_mcp_timeout_seconds
        控制（默认 60 秒）。
        """
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        messages: list[RawMessage] = []
        seen: set[str] = set()

        def collect(items: list) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                ts = _mcp_timestamp(item.get("createTime"))
                if ts is None:
                    continue
                if start_time <= ts <= end_time:
                    raw = _mcp_to_raw(item, group_id, ts)
                    if raw.source_message_id and raw.source_message_id in seen:
                        continue  # 翻页重叠，跳过重复
                    if raw.source_message_id:
                        seen.add(raw.source_message_id)
                    messages.append(raw)

        try:
            # 窗口内的每一天都取锚点（多天窗口 = 周一汇总周六+周日）
            day = start_time.date()
            last_day = end_time.date()
            while day <= last_day:
                anchor = self._anchor_for_day(group_id, day, tz)
                if anchor:
                    self._drain_around(group_id, anchor, start_time, end_time, collect)
                day = day.fromordinal(day.toordinal() + 1)
        except MCPError as e:
            return FetchResult(self.name, group_id, [], ProviderStatus.READ_FAILED, f"MCP 读取失败：{e}")
        except Exception as e:  # 防御上游异常数据
            return FetchResult(self.name, group_id, [], ProviderStatus.READ_FAILED, f"消息解析失败：{e}")

        if not messages:
            return FetchResult(self.name, group_id, [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
        return FetchResult(self.name, group_id, messages, ProviderStatus.OK)

    def _anchor_for_day(self, group_id: str, day: object, tz) -> str | None:
        """获取某天第一条消息的锚点 ID；当天无消息返回 None。"""
        structured = self._mcp_client.call(
            "wechat.chat.get_message_anchor",
            {
                "username": group_id,
                "kind": "day",
                "date": day.isoformat(),
                "source": "auto",
            },
        )
        return structured.get("anchorId") or None

    def _drain_around(
        self,
        group_id: str,
        anchor: str,
        start_time: datetime,
        end_time: datetime,
        collect,
    ) -> None:
        """从锚点向前后翻页读取，直到完全越过时间窗。

        每次调用返回锚点附近约 50 条（含锚点自身），把锚点推进到
        窗口内最前/最后一条后继续；越界或翻页重叠时停止。
        """
        # 向后（更早）方向：锚点 = 当前窗口内最早一条
        cur = anchor
        rounds = 0
        while cur and rounds < _MCP_MAX_PAGES:
            rounds += 1
            structured = self._mcp_client.call(
                "wechat.chat.get_message_around",
                {
                    "username": group_id,
                    "anchor_id": cur,
                    "before": _MCP_PAGE_SIZE,
                    "after": 0,
                    "source": "auto",
                },
            )
            items = structured.get("messages") or []
            if not items:
                break
            collect(items)
            earliest = items[0]
            ts = _mcp_timestamp(earliest.get("createTime"))
            if ts is None or ts < start_time:
                break  # 已越过窗口起点
            nxt = earliest.get("id")
            if not nxt or nxt == cur:
                break  # 锚点不再前进，防止死循环
            cur = nxt
        # 向前（更晚）方向：锚点 = 当前窗口内最晚一条
        cur = anchor
        rounds = 0
        while cur and rounds < _MCP_MAX_PAGES:
            rounds += 1
            structured = self._mcp_client.call(
                "wechat.chat.get_message_around",
                {
                    "username": group_id,
                    "anchor_id": cur,
                    "before": 0,
                    "after": _MCP_PAGE_SIZE,
                    "source": "auto",
                },
            )
            items = structured.get("messages") or []
            if not items:
                break
            collect(items)
            latest = items[-1]
            ts = _mcp_timestamp(latest.get("createTime"))
            if ts is None or ts > end_time:
                break  # 已越过窗口终点
            nxt = latest.get("id")
            if not nxt or nxt == cur:
                break  # 锚点不再前进，防止死循环
            cur = nxt


# ---------- 导出数据工具 ----------


def _safe(group_id: str) -> str:
    return "".join(c for c in group_id if c.isalnum() or c in "-_")


def _normalize_ts(raw: str) -> datetime:
    from zoneinfo import ZoneInfo

    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is not None:
        ts = ts.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return ts


def _to_raw(item: dict, ts: datetime) -> RawMessage:
    return RawMessage(
        group_id=item["group_id"],
        group_name=item.get("group_name", ""),
        sender_id=item.get("sender_id", ""),
        sender_name=item.get("sender_name", ""),
        timestamp=ts,
        message_type=item.get("message_type", "text"),
        content=item.get("content", ""),
        source=item.get("source", "wechat_data_analysis"),
        source_message_id=item.get("source_message_id", ""),
        content_hash=item.get("content_hash", ""),
    )


# ---------- MCP 响应解析 ----------


def _first_str(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in ("none", "null"):
            return text
    return ""


def _to_int(value) -> int:
    if value is None or isinstance(value, bool):
        return int(value) if value else 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return 0


def _find_list(structured: dict, keys: tuple[str, ...]) -> list | None:
    if isinstance(structured, list):
        return structured
    for key in keys:
        value = structured.get(key)
        if isinstance(value, list):
            return value
    return None


def _is_group_candidate(item: dict, username: str) -> bool:
    if username.lower().endswith("@chatroom"):
        return True
    for key in ("type", "sessionType", "chatType", "isGroup", "is_chatroom"):
        value = item.get(key)
        if isinstance(value, str) and value.strip().lower() in ("chatroom", "group", "true", "1", "群聊"):
            return True
        if value is True or value == 1:
            return True
    return False


def _parse_group_candidates(structured: dict) -> list[GroupInfo]:
    """仅把群聊候选解析为 GroupInfo；个人会话与畸形条目一律丢弃。"""
    sessions = _find_list(structured, _CANDIDATE_LIST_KEYS)
    if sessions is None:
        return []
    groups: list[GroupInfo] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        username = _first_str(item, "username", "userName", "wxid", "groupId", "id")
        if not username:
            continue
        if not _is_group_candidate(item, username):
            continue
        name = _first_str(
            item, "name", "displayName", "display_name", "remark", "remarkName", "nickname"
        )
        groups.append(
            GroupInfo(
                group_id=username,
                group_name=name or "",
                member_count=_to_int(
                    item.get("memberCount", item.get("member_count", item.get("membercount", 0)))
                ),
                extra={"source": "wechat_mcp"},
            )
        )
    return groups


def _extract_message_items(structured: dict) -> list:
    items = _find_list(structured, _ITEM_LIST_KEYS)
    return items or []


def _mcp_timestamp(raw) -> datetime | None:
    """把上游 createTime（秒/毫秒时间戳或 ISO 字符串）归一化为 Asia/Shanghai naive。"""
    from zoneinfo import ZoneInfo

    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        seconds = float(raw)
        if seconds > 1e12:  # 毫秒
            seconds /= 1000.0
        if seconds <= 0:
            return None
        try:
            return datetime.fromtimestamp(seconds, ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return _mcp_timestamp(float(text))
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return ts


def _mcp_message_type(render_type) -> str:
    if isinstance(render_type, str) and render_type.strip().isdigit():
        render_type = int(render_type.strip())
    if isinstance(render_type, int):
        return _RENDER_TYPE_MAP.get(render_type, f"render_{render_type}")
    if isinstance(render_type, str) and render_type.strip():
        return render_type
    return "text"


def _mcp_to_raw(item: dict, group_id: str, ts: datetime) -> RawMessage:
    return RawMessage(
        group_id=group_id,
        group_name="",
        sender_id=_first_str(item, "senderUsername", "sender_username", "sender", "fromUser"),
        sender_name=_first_str(
            item, "senderDisplayName", "sender_display_name", "senderName", "fromNickName"
        ),
        timestamp=ts,
        message_type=_mcp_message_type(item.get("renderType", item.get("render_type"))),
        content=str(item.get("content") or ""),
        source="wechat_data_analysis",
        source_message_id=str(item.get("id") or item.get("messageId") or ""),
        content_hash="",
    )