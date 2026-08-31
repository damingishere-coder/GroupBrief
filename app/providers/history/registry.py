"""Provider 注册与显式降级。

优先级：主 Provider → 备用 Provider → Mock（仅显式测试模式启用）。
"""

from __future__ import annotations

from app.config.settings import Settings, get_settings
from app.providers.history.base import ChatHistoryProvider, ProviderHealth
from app.providers.history.mock import MockProvider
from app.providers.history.wechat_cli import WechatCliProvider
from app.providers.history.wechat_data_analysis import WeChatDataAnalysisProvider


class ProviderConfigurationError(ValueError):
    """历史 Provider 配置无效，禁止静默改用其他实现。"""

PROVIDER_CLASSES = {
    "wechat_data_analysis": WeChatDataAnalysisProvider,
    "wechat_cli": WechatCliProvider,
    "mock": MockProvider,
}


def validate_history_provider_config(settings: Settings) -> list[str]:
    """只校验并返回 Provider 顺序，不实例化或探测外部依赖。"""
    order = [settings.history_provider_primary, settings.history_provider_fallback]
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in order:
        name = str(raw_name or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        cls = PROVIDER_CLASSES.get(name)
        if cls is None:
            raise ProviderConfigurationError(f"不支持的历史 Provider：{raw_name}")
        if name == "mock" and not settings.allow_test_providers:
            raise ProviderConfigurationError("真实运行禁止使用 mock 历史 Provider")
        names.append(name)
    if not names:
        raise ProviderConfigurationError("至少需要配置一个历史 Provider")
    return names


def build_providers(settings: Settings | None = None) -> list[ChatHistoryProvider]:
    settings = settings or get_settings()
    names = validate_history_provider_config(settings)
    providers = [PROVIDER_CLASSES[name](settings=settings) for name in names]
    if (
        settings.history_provider_mock_enabled
        and settings.allow_test_providers
        and "mock" not in names
    ):
        providers.append(MockProvider(settings=settings))
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
