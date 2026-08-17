"""历史聊天业务服务。

业务层只依赖 ChatHistoryProvider 接口，不直接依赖任何开源项目内部实现。
自动降级：主 Provider 失败 → 备用 Provider → Mock。
群名解析（resolve）路径只搜索真实的非 Mock Provider，绝不回落到 fixtures。
"""

from __future__ import annotations

import unicodedata
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

# 需移除的 Unicode 控制/变体连接码点：零宽空格/连接符与 emoji 变体选择符
_STRIP_CODEPOINTS = frozenset(
    [0x200B, 0x200C, 0x200D, 0x2060] + list(range(0xFE00, 0xFE10))
)


def normalize_name(text: str) -> str:
    """确定性群名归一化：NFKC → 大小写折叠 → 去除空白与 emoji 变体/连接码点。

    保留有意义的 CJK/ASCII 字符与基础 emoji 字符本身。
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = text.casefold()
    text = "".join(
        ch
        for ch in text
        if not ch.isspace() and ord(ch) not in _STRIP_CODEPOINTS
    )
    return text


@dataclass
class GroupMatch:
    """群名解析结果（JSON 安全）。"""

    group_id: str
    group_name: str
    member_count: int
    provider: str
    match_type: str  # exact / partial

    def to_dict(self) -> dict:
        return {
            "id": self.group_id,
            "name": self.group_name,
            "member_count": self.member_count,
            "provider": self.provider,
            "match_type": self.match_type,
        }


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

    def resolve_group_names(self, name: str) -> list[GroupMatch]:
        """按群名搜索真实群，返回规范化精确匹配优先、其次子串候选。

        只搜索非 Mock 且健康检查通过的 Provider；Mock/fixtures 永不参与。
        支持直接解析方法的 Provider（如 WeChatDataAnalysis MCP）优先走
        `resolve_groups`，其余 Provider 回落到底层 `list_groups` 全量匹配。
        """
        needle = normalize_name(name)
        exact: list[GroupMatch] = []
        partial: list[GroupMatch] = []
        seen: set[tuple[str, str]] = set()
        for provider in self.providers:
            if provider.name == "mock":
                continue
            try:
                health = provider.health_check()
                if not health.ok:
                    logger.info("resolve 跳过不可用 provider %s：%s", provider.name, health.detail)
                    continue
                resolver = getattr(provider, "resolve_groups", None)
                if callable(resolver):
                    candidates = resolver(name)
                else:
                    candidates = provider.list_groups()
                for g in candidates:
                    candidate = normalize_name(g.group_name)
                    key = (g.group_id, candidate)
                    if key in seen:
                        continue
                    seen.add(key)
                    if candidate == needle:
                        exact.append(
                            GroupMatch(
                                group_id=g.group_id,
                                group_name=g.group_name,
                                member_count=g.member_count,
                                provider=provider.name,
                                match_type="exact",
                            )
                        )
                    elif needle and needle in candidate:
                        partial.append(
                            GroupMatch(
                                group_id=g.group_id,
                                group_name=g.group_name,
                                member_count=g.member_count,
                                provider=provider.name,
                                match_type="partial",
                            )
                        )
            except Exception as e:
                logger.warning("resolve 时 provider %s 异常：%s", provider.name, str(e)[:120])
                continue
        return exact + partial
