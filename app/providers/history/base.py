"""GroupBrief 统一消息模型。

无论底层使用哪个微信读取 Provider，都必须转换为该模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ProviderStatus(str, Enum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED_WECHAT_VERSION = "UNSUPPORTED_WECHAT_VERSION"
    GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
    READ_FAILED = "READ_FAILED"
    EMPTY_RESULT = "EMPTY_RESULT"
    INVALID_RESULT = "INVALID_RESULT"


@dataclass
class ProviderHealth:
    provider: str
    status: ProviderStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ProviderStatus.OK


@dataclass
class GroupInfo:
    group_id: str
    group_name: str = ""
    member_count: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class RawMessage:
    """微信读取 Provider 返回的原始消息（已最小结构化）。"""

    group_id: str
    group_name: str
    sender_id: str
    sender_name: str
    timestamp: datetime
    message_type: str = "text"
    content: str = ""
    source: str = ""
    source_message_id: str = ""
    content_hash: str = ""


@dataclass
class FetchResult:
    provider: str
    group_id: str
    messages: list[RawMessage]
    status: ProviderStatus = ProviderStatus.OK
    detail: str = ""
    meta: dict = field(default_factory=dict)


class ChatHistoryProvider:
    """统一历史聊天数据接口。业务层只能依赖此接口。"""

    name: str = "base"

    def health_check(self) -> ProviderHealth:
        raise NotImplementedError

    def list_groups(self) -> list[GroupInfo]:
        raise NotImplementedError

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        raise NotImplementedError
