"""WeChatDataAnalysis Provider（主读取路线）。

GroupBrief 不关心 WeChatDataAnalysis 内部如何解析微信数据库，
只读取它导出的结构化数据（JSON），数据格式与统一消息模型一致。

支持两种数据来源：
1. 导出目录：WeChatDataAnalysis 导出后的 JSON 文件目录
2. 微信原始数据目录探测：仅用于 health_check 状态提示

当真实数据不可用时返回明确状态（UNAVAILABLE / UNSUPPORTED_WECHAT_VERSION），
由上层自动降级到 wechat-cli 或 Mock。
"""

from __future__ import annotations

import json
import os
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

DEFAULT_WECHAT_DIRS = [
    Path(os.environ.get("USERPROFILE", "")) / "Documents" / "WeChat Files",
    Path(os.environ.get("USERPROFILE", "")) / "Documents" / "xwechat_files",
]


def _load_export_file(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class WeChatDataAnalysisProvider(ChatHistoryProvider):
    name = "wechat_data_analysis"

    def __init__(self, export_dir: Path | None = None):
        settings: Settings = get_settings()
        self.export_dir = export_dir or settings.data_dir / "wechat_export"
        self._groups_cache: list[GroupInfo] | None = None

    def health_check(self) -> ProviderHealth:
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
                f"已发现微信数据目录 {wechat_dir}，但当前环境未安装/未配置 "
                "WeChatDataAnalysis 导出工具。请使用 WeChatDataAnalysis 导出群聊 JSON "
                f"到 {self.export_dir}（含 groups.json 与 messages/ 目录），即可启用。",
            )
        return ProviderHealth(
            self.name,
            ProviderStatus.UNAVAILABLE,
            "未找到微信数据目录。请安装微信并登录，或用 WeChatDataAnalysis 导出数据。",
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

    def list_groups(self) -> list[GroupInfo]:
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

    def fetch_messages(
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
