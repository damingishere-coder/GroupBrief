"""Prompt 生成服务。

Codex GPT 主负责：群聊内容 → 理解事件 → 整理话题 → 生成 GPT 生图 Prompt；
主调用失败时使用 DeepSeek 备用。
不负责排行榜计算 / 微信读取 / 邮件 / 调度。
主备都不可用时优雅降级到本地模板，不阻塞其余流程。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.conversation_segments import PromptMessage
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Group
from app.providers.ai.base import ImagePromptResult, PromptGeneratorProvider
from app.providers.ai.codex import build_summary_provider
from app.providers.ai.template import TemplatePromptProvider
from app.scheduler.calendar_rules import ReportWindow
from app.services.message_normalizer import NormalizedMessage
from app.services.ranking_service import RankingResult

logger = get_logger("groupbrief.ai")


@dataclass
class PromptOutcome:
    success: bool
    prompt: str = ""
    error: str = ""
    meta: dict | None = None


class PromptService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._provider: PromptGeneratorProvider | None = None

    def _get_provider(self) -> PromptGeneratorProvider:
        if self._provider is not None:
            return self._provider
        primary = (self.settings.summary_provider_primary or "codex").strip().lower()
        if primary == "deepseek" and not self.settings.ai_api_key:
            self._provider = TemplatePromptProvider()
        else:
            self._provider = build_summary_provider(self.settings)
        return self._provider

    def generate(
        self,
        group: Group,
        window: ReportWindow,
        ranking: RankingResult,
        normalized: list[NormalizedMessage],
    ) -> PromptOutcome:
        provider = self._get_provider()

        message_items = self._build_message_items(normalized)
        context_text = "\n".join(
            f"[{item.timestamp.strftime('%H:%M') if item.timestamp else ''}] {item.sender_name}: {item.text}"
            for item in message_items
        )
        context = provider.build_context(
            group_id=group.wechat_group_id or str(group.id),
            group_name=group.display_name or group.wechat_group_name,
            report_date=window.report_date.isoformat(),
            range_start=window.range_start.strftime("%Y-%m-%d %H:%M:%S"),
            range_end=window.range_end.strftime("%Y-%m-%d %H:%M:%S"),
            total_messages=ranking.total_messages,
            speaker_count=ranking.speaker_count,
            messages_text=context_text,
            message_items=message_items,
            weekdays_text="",
        )
        result: ImagePromptResult = provider.generate_image_prompt(context)
        if result.success:
            return PromptOutcome(True, result.prompt, meta=result.meta)
        if provider.name != "template":
            template_result = TemplatePromptProvider().generate_image_prompt(context)
            if template_result.success:
                logger.warning("主备模型均未完成 Prompt，V1 已降级到本地模板")
                meta = dict(template_result.meta or {})
                meta.update({"fallback": "template", "degraded_from": provider.name})
                return PromptOutcome(True, template_result.prompt, meta=meta)
        logger.error("Prompt 生成失败：%s", result.error)
        return PromptOutcome(False, "", result.error, result.meta)

    def _build_context_text(
        self, normalized: list[NormalizedMessage], ranking: RankingResult
    ) -> str:
        """兼容旧调用：返回全部可统计文本，不再截断聊天尾部。"""
        return "\n".join(
            f"[{item.timestamp.strftime('%H:%M') if item.timestamp else ''}] {item.sender_name}: {item.text}"
            for item in self._build_message_items(normalized)
        )

    @staticmethod
    def _build_message_items(normalized: list[NormalizedMessage]) -> list[PromptMessage]:
        result: list[PromptMessage] = []
        for index, message in enumerate(normalized, start=1):
            if not message.countable:
                continue
            text = (message.ai_text or message.content or "").strip()
            if not text:
                continue
            result.append(
                PromptMessage(
                    message_id=message.content_hash or f"v1-{index}",
                    timestamp=message.timestamp,
                    sender_name=message.sender_name or "(未知)",
                    text=text,
                )
            )
        return result
