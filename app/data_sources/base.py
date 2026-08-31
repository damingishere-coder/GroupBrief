"""V2 数据源抽象（P1 实现）。

GroupBrief V2 不直接耦合任何微信项目内部实现。
业务层（RankingEngine / Pipeline）只依赖 WeChatDataSource 接口。

路线文档接口约定：
    class WeChatDataSource:
        def health_check(self): ...
        def resolve_group(self, group_name): ...
        def fetch_messages(self, group_id, start_time, end_time): ...

本模块同时定义 GroupBrief 自己的 Message Schema（V2 输出 messages.json 的结构）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DataSourceStatus(str, Enum):
    OK = "OK"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
    EMPTY_RESULT = "EMPTY_RESULT"
    READ_FAILED = "READ_FAILED"


@dataclass
class DataSourceHealth:
    status: DataSourceStatus
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == DataSourceStatus.OK


@dataclass
class ResolvedGroup:
    """群名解析结果。"""

    group_id: str
    group_name: str = ""
    member_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class V2Message:
    """V2 Message Schema（对应路线文档 P1 约定）。

    至少包含：message_id / group_id / group_name / sender_id / sender_name /
    timestamp / message_type / content。额外字段保留在 raw 中。
    """

    message_id: str
    group_id: str
    group_name: str
    sender_id: str
    sender_name: str
    timestamp: datetime
    message_type: str = "text"
    content: str = ""
    upstream_sender_name: str = ""
    sender_name_source: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp.isoformat(),
            "message_type": self.message_type,
            "content": self.content,
            "upstream_sender_name": self.upstream_sender_name,
            "sender_name_source": self.sender_name_source,
        }


@dataclass
class FetchResult:
    messages: list[V2Message]
    status: DataSourceStatus = DataSourceStatus.OK
    detail: str = ""
    error_type: str = ""  # V2 错误类型（见 app.v2.constants）
    meta: dict[str, Any] = field(default_factory=dict)


class WeChatDataSource:
    """V2 统一聊天数据源接口。"""

    name: str = "base"

    def health_check(self) -> DataSourceHealth:
        raise NotImplementedError

    def list_groups(self) -> list[ResolvedGroup]:
        """列出可解析的群（用于绑定群）。"""
        raise NotImplementedError

    def resolve_group(self, group_name: str) -> list[ResolvedGroup]:
        """按群名解析真实群（返回匹配候选）。"""
        raise NotImplementedError

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        """按群 + 时间段获取标准化消息。"""
        raise NotImplementedError
