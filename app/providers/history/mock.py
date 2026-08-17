"""Mock Provider：从 fixtures 读取模拟聊天数据。

数据与正式模型完全一致，用于真实微信不可用时继续开发全链路。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.providers.history.base import (
    ChatHistoryProvider,
    FetchResult,
    GroupInfo,
    ProviderHealth,
    ProviderStatus,
    RawMessage,
)


def safe_group_dir(group_id: str) -> str:
    return "".join(c for c in group_id if c.isalnum() or c in "-_")


def _normalize_ts(raw: str) -> datetime:
    """将带时区的时间戳归一化为 Asia/Shanghai 的 naive datetime。"""
    from zoneinfo import ZoneInfo

    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is not None:
        ts = ts.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return ts


class MockProvider(ChatHistoryProvider):
    name = "mock"

    def __init__(self, fixtures_dir: Path | None = None):
        settings: Settings = get_settings()
        self.fixtures_dir = fixtures_dir or settings.fixtures_dir

    def health_check(self) -> ProviderHealth:
        if (self.fixtures_dir / "groups.json").exists():
            return ProviderHealth(self.name, ProviderStatus.OK, "fixtures 可用")
        return ProviderHealth(self.name, ProviderStatus.UNAVAILABLE, "缺少 fixtures/groups.json")

    def list_groups(self) -> list[GroupInfo]:
        groups_file = self.fixtures_dir / "groups.json"
        if not groups_file.exists():
            return []
        data = json.loads(groups_file.read_text(encoding="utf-8"))
        return [
            GroupInfo(group_id=g["group_id"], group_name=g.get("group_name", ""), member_count=g.get("member_count", 0))
            for g in data
        ]

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        day_dir = self.fixtures_dir / "messages" / safe_group_dir(group_id)
        if not day_dir.exists():
            return FetchResult(self.name, group_id, [], ProviderStatus.GROUP_NOT_FOUND, f"未找到 {group_id} 的 fixture 数据")

        messages: list[RawMessage] = []
        day = start_time.date()
        last_day = end_time.date()
        while day <= last_day:
            file = day_dir / f"{day.isoformat()}.json"
            if file.exists():
                raw_list = json.loads(file.read_text(encoding="utf-8"))
                for item in raw_list:
                    ts = _normalize_ts(item["timestamp"])
                    if start_time <= ts <= end_time:
                        messages.append(
                            RawMessage(
                                group_id=item["group_id"],
                                group_name=item["group_name"],
                                sender_id=item["sender_id"],
                                sender_name=item["sender_name"],
                                timestamp=ts,
                                message_type=item.get("message_type", "text"),
                                content=item.get("content", ""),
                                source=item.get("source", "mock_fixture"),
                                source_message_id=item.get("source_message_id", ""),
                                content_hash=item.get("content_hash", ""),
                            )
                        )
            day = day.fromordinal(day.toordinal() + 1)

        if not messages:
            return FetchResult(self.name, group_id, [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
        return FetchResult(self.name, group_id, messages, ProviderStatus.OK)
