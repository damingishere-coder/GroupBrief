"""V2 WeChatDataAnalysis 数据源实现。

包装 V1 `WeChatDataAnalysisProvider`（其已打通本机 WeChatDataAnalysis 本地
MCP 服务：wechat.core.get_status / wechat.chat.list_sessions / resolve_session
/ get_message_anchor + get_message_around 锚点翻页），并把结果转换为 V2
`WeChatDataSource` 接口与 V2 Message Schema。

设计约束：
- 只读取数据，绝不修改微信数据；
- MCP 工具名、API 路径集中在 V1 provider 与 `wechat_mcp.py`，本模块不散落；
- 群名解析与消息读取一律带 `source=auto`（优先本地 API 自动识别账号）；
- 失败时返回 V2 错误类型（WECHAT_DATA_UNAVAILABLE / GROUP_NOT_FOUND /
  MESSAGE_FETCH_FAILED），业务层可据此判定。
"""

from __future__ import annotations

from datetime import datetime

from app.config.settings import Settings, get_settings
from app.v2.constants import (
    GROUP_NOT_FOUND,
    MESSAGE_FETCH_FAILED,
    WECHAT_DATA_UNAVAILABLE,
)
from app.data_sources.base import (
    DataSourceHealth,
    DataSourceStatus,
    FetchResult,
    ResolvedGroup,
    V2Message,
    WeChatDataSource,
)
from app.providers.history.base import ProviderStatus, RawMessage
from app.providers.history.wechat_data_analysis import WeChatDataAnalysisProvider

# 默认本机地址（与 .env 一致；覆盖时仅允许本机回环，见 V1 wechat_mcp.py）
DEFAULT_MCP_URL = "http://127.0.0.1:10392/mcp"


class WeChatDataAnalysisSource(WeChatDataSource):
    """V2 数据源：WeChatDataAnalysis 本地 MCP（优先）与 JSON 导出（回退）。"""

    name = "wechat_data_analysis"

    def __init__(
        self,
        settings: Settings | None = None,
        provider: WeChatDataAnalysisProvider | None = None,
    ):
        self.settings = settings or get_settings()
        # 复用 V1 Provider：内部处理 MCP 客户端构造 / 导出目录探测 / 联系人解析。
        # provider 参数用于测试注入假实现，避免触发真实 MCP。
        self._provider = provider or WeChatDataAnalysisProvider(settings=self.settings)

    # ---------- health_check ----------

    def health_check(self) -> DataSourceHealth:
        health = self._provider.health_check()
        if health.ok:
            return DataSourceHealth(DataSourceStatus.OK, health.detail)
        status = DataSourceStatus.UNAVAILABLE
        if health.status == ProviderStatus.GROUP_NOT_FOUND:
            status = DataSourceStatus.GROUP_NOT_FOUND
        return DataSourceHealth(status, health.detail)

    # ---------- 群列表 / 解析 ----------

    def list_groups(self) -> list[ResolvedGroup]:
        groups = self._provider.list_groups()
        return [_to_resolved_group(g.group_id, g.group_name, g.member_count) for g in groups]

    def resolve_group(self, group_name: str) -> list[ResolvedGroup]:
        candidates = self._provider.resolve_groups(group_name)
        return [
            _to_resolved_group(g.group_id, g.group_name, g.member_count)
            for g in candidates
        ]

    # ---------- 群存在性校验 ----------

    def _group_exists(self, group_id: str) -> bool:
        """群 ID 是否存在于当前账号（仅 MCP 可用时有效；导出模式同样支持）。"""
        try:
            for g in self._provider.list_groups():
                if g.group_id == group_id:
                    return True
        except Exception:
            pass
        return False

    # ---------- 消息读取 ----------

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        result = self._provider.fetch_messages(group_id, start_time, end_time)

        if result.status == ProviderStatus.OK and result.messages:
            messages = [_to_v2_message(m) for m in result.messages]
            return FetchResult(
                messages=messages,
                status=DataSourceStatus.OK,
                detail=f"{self.name}：{len(messages)} 条消息",
                meta=result.meta,
            )
        if result.status == ProviderStatus.GROUP_NOT_FOUND:
            return FetchResult(
                [], DataSourceStatus.GROUP_NOT_FOUND, result.detail, GROUP_NOT_FOUND, result.meta
            )
        if result.status == ProviderStatus.EMPTY_RESULT:
            # 上游对不存在的群与「存在但无消息」都返回空；校验群是否存在，
            # 不存在 → GROUP_NOT_FOUND，存在但真的无消息 → EMPTY_RESULT。
            if not self._group_exists(group_id):
                return FetchResult(
                    [], DataSourceStatus.GROUP_NOT_FOUND,
                    f"群 {group_id} 不存在于当前微信账号",
                    GROUP_NOT_FOUND,
                    result.meta,
                )
            return FetchResult(
                [], DataSourceStatus.EMPTY_RESULT, result.detail, "", result.meta
            )
        # READ_FAILED / UNAVAILABLE 等一律归为取数失败
        error_type = WECHAT_DATA_UNAVAILABLE if result.status in (
            ProviderStatus.UNAVAILABLE,
            ProviderStatus.UNSUPPORTED_WECHAT_VERSION,
        ) else MESSAGE_FETCH_FAILED
        return FetchResult(
            [], DataSourceStatus.READ_FAILED, result.detail, error_type, result.meta
        )


# ---------- 转换工具 ----------


def _to_resolved_group(group_id: str, group_name: str, member_count: int) -> ResolvedGroup:
    return ResolvedGroup(
        group_id=group_id,
        group_name=group_name or "",
        member_count=member_count or 0,
        extra={"source": "wechat_data_analysis"},
    )


def _to_v2_message(m: RawMessage) -> V2Message:
    """RawMessage → V2 Message Schema。

    message_id 优先取上游 source_message_id；缺失时退回 content_hash，
    再缺失则用发送者+时间戳+内容的确定性摘要，保证去重与归档稳定。
    """
    message_id = m.source_message_id or m.content_hash or _stable_id(m)
    return V2Message(
        message_id=message_id,
        group_id=m.group_id,
        group_name=m.group_name or "",
        sender_id=m.sender_id or "",
        sender_name=m.sender_name or "(未知)",
        timestamp=m.timestamp,
        message_type=m.message_type,
        content=m.content,
        raw={
            "source": m.source,
            "source_message_id": m.source_message_id,
            "content_hash": m.content_hash,
        },
    )


def _stable_id(m: RawMessage) -> str:
    import hashlib

    payload = f"{m.sender_id}|{m.timestamp.isoformat()}|{m.content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]
