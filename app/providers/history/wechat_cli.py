"""wechat-cli Provider（备用读取路线）。

通过外部命令行工具 wechat-cli 导出聊天记录。
契约：wechat-cli export --group <id> --start <ISO> --end <ISO> --output <json>
当命令不可用时返回明确状态，由上层自动降级。
"""

from __future__ import annotations

import json
import shutil
import subprocess
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


class WechatCliProvider(ChatHistoryProvider):
    name = "wechat_cli"

    def __init__(
        self,
        cli_path: str | None = None,
        settings: Settings | None = None,
    ):
        settings = settings or get_settings()
        self.cli_path = cli_path or settings.wechat_cli_path or "wechat-cli"
        self.export_dir = settings.data_dir / "wechat_cli_export"

    def _binary(self) -> str | None:
        if self.cli_path and self.cli_path != "wechat-cli":
            return self.cli_path if Path(self.cli_path).exists() else None
        return self.cli_path if shutil.which(self.cli_path) else None

    def health_check(self) -> ProviderHealth:
        binary = self._binary()
        if binary is None:
            return ProviderHealth(
                self.name,
                ProviderStatus.UNAVAILABLE,
                "未找到 wechat-cli 命令。请安装 wechat-cli 或在设置中配置其路径。",
            )
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return ProviderHealth(self.name, ProviderStatus.OK, result.stdout.strip()[:120])
            return ProviderHealth(self.name, ProviderStatus.READ_FAILED, f"命令执行失败: {result.stderr[:200]}")
        except Exception as e:
            return ProviderHealth(self.name, ProviderStatus.READ_FAILED, str(e)[:200])

    def list_groups(self) -> list[GroupInfo]:
        binary = self._binary()
        if binary is None:
            return []
        try:
            result = subprocess.run(
                [binary, "list-groups", "--json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return []
            data = json.loads(result.stdout)
            return [
                GroupInfo(group_id=g["group_id"], group_name=g.get("group_name", ""), member_count=g.get("member_count", 0))
                for g in data
            ]
        except Exception:
            return []

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchResult:
        binary = self._binary()
        if binary is None:
            return FetchResult(self.name, group_id, [], ProviderStatus.UNAVAILABLE, "wechat-cli 不可用")

        self.export_dir.mkdir(parents=True, exist_ok=True)
        out_file = self.export_dir / f"{_safe(group_id)}.json"
        try:
            result = subprocess.run(
                [
                    binary,
                    "export",
                    "--group",
                    group_id,
                    "--start",
                    start_time.isoformat(),
                    "--end",
                    end_time.isoformat(),
                    "--output",
                    str(out_file),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                return FetchResult(self.name, group_id, [], ProviderStatus.READ_FAILED, result.stderr[:300])
            if not out_file.exists():
                return FetchResult(self.name, group_id, [], ProviderStatus.EMPTY_RESULT, "导出文件为空")
            raw_list = json.loads(out_file.read_text(encoding="utf-8"))
            messages = []
            for item in raw_list:
                ts = datetime.fromisoformat(item["timestamp"])
                messages.append(
                    RawMessage(
                        group_id=item["group_id"],
                        group_name=item.get("group_name", ""),
                        sender_id=item.get("sender_id", ""),
                        sender_name=item.get("sender_name", ""),
                        timestamp=ts,
                        message_type=item.get("message_type", "text"),
                        content=item.get("content", ""),
                        source="wechat_cli",
                        source_message_id=item.get("source_message_id", ""),
                        content_hash=item.get("content_hash", ""),
                    )
                )
            if not messages:
                return FetchResult(self.name, group_id, [], ProviderStatus.EMPTY_RESULT, "该时间段无消息")
            return FetchResult(self.name, group_id, messages, ProviderStatus.OK)
        except Exception as e:
            return FetchResult(self.name, group_id, [], ProviderStatus.READ_FAILED, str(e)[:300])


def _safe(group_id: str) -> str:
    return "".join(c for c in group_id if c.isalnum() or c in "-_")
