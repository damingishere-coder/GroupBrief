"""把 V1 历史 Provider 安全适配到 V2 只读数据源契约。"""

from __future__ import annotations

import hashlib

from app.data_sources.base import (
    DataSourceHealth,
    DataSourceStatus,
    FetchResult,
    ResolvedGroup,
    V2Message,
    WeChatDataSource,
)
from app.providers.history.base import ChatHistoryProvider, ProviderStatus, RawMessage
from app.v2.constants import GROUP_NOT_FOUND, MESSAGE_FETCH_FAILED, WECHAT_DATA_UNAVAILABLE


class HistoryProviderDataSource(WeChatDataSource):
    def __init__(self, provider: ChatHistoryProvider):
        self.provider = provider
        self.name = provider.name

    def health_check(self) -> DataSourceHealth:
        health = self.provider.health_check()
        status = DataSourceStatus.OK if health.ok else DataSourceStatus.UNAVAILABLE
        return DataSourceHealth(status, health.detail)

    def list_groups(self) -> list[ResolvedGroup]:
        return [
            ResolvedGroup(item.group_id, item.group_name, item.member_count)
            for item in self.provider.list_groups()
        ]

    def resolve_group(self, group_name: str) -> list[ResolvedGroup]:
        query = group_name.strip().casefold()
        return [
            item
            for item in self.list_groups()
            if query in item.group_name.casefold()
        ]

    def fetch_messages(self, group_id, start_time, end_time) -> FetchResult:
        result = self.provider.fetch_messages(group_id, start_time, end_time)
        if result.status == ProviderStatus.OK and result.messages:
            return FetchResult(
                [_message(item) for item in result.messages],
                DataSourceStatus.OK,
                result.detail,
                meta={
                    **(result.meta if isinstance(result.meta, dict) else {}),
                    "provider_chain": [self.name],
                    "fallback_used": False,
                },
            )
        status = (
            DataSourceStatus.GROUP_NOT_FOUND
            if result.status == ProviderStatus.GROUP_NOT_FOUND
            else DataSourceStatus.EMPTY_RESULT
            if result.status == ProviderStatus.EMPTY_RESULT
            else DataSourceStatus.UNAVAILABLE
            if result.status == ProviderStatus.UNAVAILABLE
            else DataSourceStatus.READ_FAILED
        )
        error_type = (
            GROUP_NOT_FOUND
            if status == DataSourceStatus.GROUP_NOT_FOUND
            else WECHAT_DATA_UNAVAILABLE
            if status == DataSourceStatus.UNAVAILABLE
            else MESSAGE_FETCH_FAILED
        )
        return FetchResult([], status, result.detail, error_type, {"provider_chain": [self.name]})


def _message(item: RawMessage) -> V2Message:
    message_id = item.source_message_id or item.content_hash
    if not message_id:
        payload = f"{item.sender_id}|{item.timestamp.isoformat()}|{item.content}"
        message_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return V2Message(
        message_id=message_id,
        group_id=item.group_id,
        group_name=item.group_name,
        sender_id=item.sender_id,
        sender_name=item.sender_name or "(未知)",
        timestamp=item.timestamp,
        message_type=item.message_type,
        content=item.content,
        raw={"source": item.source or "history_provider"},
    )
