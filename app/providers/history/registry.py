"""Provider 注册与自动降级。

优先级：主 Provider → 备用 Provider → Mock（仅开发模式启用）。
"""

from __future__ import annotations

from app.config.settings import Settings, get_settings
from app.providers.history.base import ChatHistoryProvider, ProviderHealth
from app.providers.history.mock import MockProvider
from app.providers.history.wechat_cli import WechatCliProvider
from app.providers.history.wechat_data_analysis import WeChatDataAnalysisProvider

PROVIDER_CLASSES = {
    "wechat_data_analysis": WeChatDataAnalysisProvider,
    "wechat_cli": WechatCliProvider,
    "mock": MockProvider,
}


def build_providers(settings: Settings | None = None) -> list[ChatHistoryProvider]:
    settings = settings or get_settings()
    providers: list[ChatHistoryProvider] = []
    order = [settings.history_provider_primary, settings.history_provider_fallback]
    seen: set[str] = set()
    for name in order:
        if not name or name in seen:
            continue
        seen.add(name)
        cls = PROVIDER_CLASSES.get(name)
        if cls:
            providers.append(cls())
    if settings.history_provider_mock_enabled and "mock" not in seen:
        providers.append(MockProvider())
    return providers


def check_all_health(settings: Settings | None = None) -> dict[str, ProviderHealth]:
    result: dict[str, ProviderHealth] = {}
    for provider in build_providers(settings):
        try:
            result[provider.name] = provider.health_check()
        except Exception as e:
            from app.providers.history.base import ProviderStatus

            result[provider.name] = ProviderHealth(provider.name, ProviderStatus.UNAVAILABLE, str(e)[:200])
    return result
