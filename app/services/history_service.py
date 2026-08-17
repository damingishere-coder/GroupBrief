"""历史聊天业务服务。

业务层只依赖 ChatHistoryProvider 接口，不直接依赖任何开源项目内部实现。
自动降级：主 Provider 失败 → 备用 Provider → Mock。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.providers.history.base import (
    FetchResult,
    GroupInfo,
    ProviderHealth,
    ProviderStatus,
    RawMessage,
)
from app.providers.history.registry import build_providers, check_all_health

logger = get_logger("groupbrief.providers")


@dataclass
class FetchOutcome:
    provider: str
    messages: list[RawMessage]
    status: ProviderStatus
    detail: str = ""


class HistoryService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.providers = build_providers(self.settings)

    def all_health(self) -> dict[str, ProviderHealth]:
        return check_all_health(self.settings)

    def fetch(
        self,
        group_id: str,
        group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> FetchOutcome:
        errors: list[str] = []
        for provider in self.providers:
            try:
                health = provider.health_check()
                if not health.ok:
                    logger.info("provider %s 不可用：%s", provider.name, health.detail)
                    errors.append(f"{provider.name}: {health.detail[:120]}")
                    continue
                result: FetchResult = provider.fetch_messages(group_id, start_time, end_time)
                if result.status in (
                    ProviderStatus.OK,
                    ProviderStatus.EMPTY_RESULT,
                ):
                    if result.messages:
                        logger.info(
                            "provider %s 读取 %s：%d 条消息",
                            provider.name,
                            group_name,
                            len(result.messages),
                        )
                    return FetchOutcome(
                        provider=provider.name,
                        messages=result.messages,
                        status=result.status,
                        detail=result.detail,
                    )
                errors.append(f"{provider.name}: {result.status.value} {result.detail[:120]}")
                logger.warning("provider %s 读取失败：%s", provider.name, result.detail[:200])
            except Exception as e:
                errors.append(f"{provider.name}: {str(e)[:120]}")
                logger.exception("provider %s 异常", provider.name)

        return FetchOutcome(
            provider="none",
            messages=[],
            status=ProviderStatus.READ_FAILED,
            detail="；".join(errors)[:500],
        )

    def discover_groups(self) -> list[GroupInfo]:
        """从可用 Provider 获取群列表（用于添加群时选择）。"""
        seen: dict[str, GroupInfo] = {}
        for provider in self.providers:
            try:
                health = provider.health_check()
                if not health.ok:
                    continue
                for g in provider.list_groups():
                    if g.group_id not in seen:
                        seen[g.group_id] = g
            except Exception:
                continue
        return list(seen.values())
