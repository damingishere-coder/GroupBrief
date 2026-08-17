"""Prompt 生成服务。

DeepSeek V4 Flash 只负责：群聊内容 → 理解事件 → 整理话题 → 生成 GPT 生图 Prompt。
不负责排行榜计算 / 微信读取 / 邮件 / 调度。
无 API Key 时优雅降级（标记 skipped），不阻塞其余流程。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Group
from app.providers.ai.base import ImagePromptResult, PromptGeneratorProvider
from app.providers.ai.deepseek import DeepSeekV4FlashProvider
from app.scheduler.calendar_rules import ReportWindow
from app.services.message_normalizer import NormalizedMessage
from app.services.ranking_service import RankingResult

logger = get_logger("groupbrief.ai")


@dataclass
class PromptOutcome:
    success: bool
    prompt: str = ""
    error: str = ""


class PromptService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._provider: PromptGeneratorProvider | None = None

    def _get_provider(self) -> PromptGeneratorProvider:
        if self._provider is not None:
            return self._provider
        if self.settings.ai_api_key and self.settings.ai_provider == "deepseek":
            self._provider = DeepSeekV4FlashProvider(self.settings)
        else:
            # 未配置 API Key 时使用本地模板，保证全链路可交付
            from app.providers.ai.template import TemplatePromptProvider

            self._provider = TemplatePromptProvider()
        return self._provider

    def generate(
        self,
        group: Group,
        window: ReportWindow,
        ranking: RankingResult,
        normalized: list[NormalizedMessage],
    ) -> PromptOutcome:
        provider = self._get_provider()

        context_text = self._build_context_text(normalized, ranking)
        context = provider.build_context(
            group_id=group.wechat_group_id or str(group.id),
            group_name=group.display_name or group.wechat_group_name,
            report_date=window.report_date.isoformat(),
            range_start=window.range_start.strftime("%Y-%m-%d %H:%M:%S"),
            range_end=window.range_end.strftime("%Y-%m-%d %H:%M:%S"),
            total_messages=ranking.total_messages,
            speaker_count=ranking.speaker_count,
            messages_text=context_text,
            weekdays_text="周一的周末两天汇总，标题倾向：群里热闹这两天！" if window.is_weekend_summary else "",
        )
        result: ImagePromptResult = provider.generate_image_prompt(context)
        if result.success:
            return PromptOutcome(True, result.prompt)
        logger.error("Prompt 生成失败：%s", result.error)
        return PromptOutcome(False, "", result.error)

    def _build_context_text(
        self, normalized: list[NormalizedMessage], ranking: RankingResult
    ) -> str:
        """整理消息文本：过滤系统消息、截断到 MAX_CONTEXT_CHARS。"""
        lines: list[str] = []
        for m in normalized:
            if not m.countable:
                continue
            ts = m.timestamp.strftime("%H:%M")
            text = m.ai_text or m.content or ""
            lines.append(f"[{ts}] {m.sender_name}: {text}")
            if sum(len(l) for l in lines) > self.settings.max_context_chars:
                break
        return "\n".join(lines)
