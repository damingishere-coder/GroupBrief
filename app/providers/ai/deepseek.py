"""DeepSeek V4 Flash Provider。

职责：根据整理好的群聊内容生成「可直接粘贴给 GPT 生图」的漫画日报 Prompt。
- 超长内容按 CHUNK_MESSAGE_COUNT 分块，逐块分析事件，再合并
- 严格约束：不得编造聊天中不存在的事件 / 人物 / 金额 / 时间 / 地点
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from app.config.settings import Settings
from app.core.logging import get_logger
from app.providers.ai.base import (
    ImagePromptResult,
    PromptContext,
    PromptGeneratorProvider,
)

logger = get_logger("groupbrief.ai")

SYSTEM_PROMPT = """你是「群报 GroupBrief」的漫画日报海报 Prompt 设计师。
你的唯一任务：根据给定的微信群聊内容，生成一份可以直接复制给 GPT 图片生成能力的完整中文 Prompt，
用于绘制「竖版微信群日报漫画信息图」。

硬性要求（必须严格遵守）：
1. 只能使用聊天内容中真实存在的事件、人物、对话，禁止编造任何聊天中不存在的事件。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 原话引用必须来自真实聊天，可适当缩写，但不能改写事实。
4. 可以幽默化标题，但不能改变事实。
5. 海报人物依据「聊天事件中提到的人物」，而不是发言排行榜 Top10。
6. 输出结构固定：
【任务】
【群名称】
【统计时间】
【数据】（消息数、发言人数，必须使用给定数字，禁止自行计算）
【主标题】
【副标题】
【整体视觉】（配色、风格、构图）
【版面1】~【版面5-8】（标题/事件/代表人物/建议画面/可用文字）
【底部总结】
【硬性要求】"""

CHUNK_ANALYZE_PROMPT = """以下是微信群聊记录片段（{label}）。

请分析并输出 JSON（不要输出其他内容）：
{{
  "events": [
    {{"title": "事件短标题", "people": ["提到的人名"], "content": "事件描述（真实基于聊天）", "quotes": ["1-3条真实原话或改写原话"]}}
  ]
}}

要求：只提取真实存在的内容；没有事件就返回空数组；不超过 6 个事件。"""

MERGE_PROMPT = """你已分别分析了微信群「{group_name}」在 {range_start} ~ {range_end} 的聊天记录片段，
每个片段的 JSON 分析结果如下：

{chunk_results}

请合并去重这些事件（相似事件合并，保留真实细节），然后生成一份完整的、
可直接复制给 GPT 图片生成能力的漫画日报海报 Prompt。

{weekday_hint}
必须遵守：不编造、不添加聊天中不存在的内容；数据（{total_messages} 条消息、{speaker_count} 人发言）必须原样使用。
按以下结构输出：
【任务】生成一张竖版微信群日报漫画信息图。
【群名称】
【统计时间】
【数据】
【主标题】（幽默有趣，可结合当天梗）
【副标题】
【整体视觉】（蓝白+多彩漫画风格，画面分区清晰）
【版面1】~【版面N】（每个版面：标题/事件/代表人物/建议画面/可用文字；选取 5~8 个主要话题）
【底部总结】（一句话文案）
【硬性要求】"""


@dataclass
class _ChunkResult:
    text: str


class DeepSeekV4FlashProvider(PromptGeneratorProvider):
    name = "deepseek"
    model = "deepseek-chat"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.ai_model or self.model

    def build_context(
        self,
        group_id: str,
        group_name: str,
        report_date: str,
        range_start: str,
        range_end: str,
        total_messages: int,
        speaker_count: int,
        messages_text: str,
        weekdays_text: str = "",
    ) -> PromptContext:
        return PromptContext(
            group_id=group_id,
            group_name=group_name,
            report_date=report_date,
            range_start=range_start,
            range_end=range_end,
            total_messages=total_messages,
            speaker_count=speaker_count,
            messages_text=messages_text,
            weekdays_text=weekdays_text,
        )

    def health_check(self) -> tuple[bool, str]:
        if not self.settings.ai_api_key:
            return False, "未配置 AI_API_KEY"
        return True, f"已配置（{self.model}）"

    def generate_image_prompt(self, context: PromptContext) -> ImagePromptResult:
        try:
            messages = context.messages_text.split("\n")
            chunks = self._chunk_messages(messages)
            logger.info("群 %s：共 %d 行消息，分 %d 块", context.group_name, len(messages), len(chunks))

            if len(chunks) == 1:
                return self._merge(context, [chunks[0].text])

            # 分块分析
            chunk_results: list[str] = []
            for idx, chunk in enumerate(chunks, start=1):
                chunk_results.append(
                    self._analyze_chunk(chunk, f"第 {idx}/{len(chunks)} 块")
                )
            return self._merge(context, chunk_results)
        except Exception as e:
            logger.exception("DeepSeek 调用失败")
            return ImagePromptResult(False, error=str(e)[:300], provider=self.name, model=self.model)

    def _chunk_messages(self, lines: list[str]) -> list[_ChunkResult]:
        if not lines:
            return []
        chunk_size = max(1, self.settings.chunk_message_count)
        chunks: list[_ChunkResult] = []
        for i in range(0, len(lines), chunk_size):
            chunks.append(_ChunkResult(text="\n".join(lines[i : i + chunk_size])))
        return chunks

    def _analyze_chunk(self, chunk_text: str, label: str) -> str:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": CHUNK_ANALYZE_PROMPT.format(label=label) + "\n\n聊天记录：\n" + chunk_text},
        ]
        text = self._chat(messages)
        return text

    def _merge(self, context: PromptContext, chunk_results: list[str]) -> ImagePromptResult:
        merged = "\n\n".join(chunk_results)
        user_prompt = MERGE_PROMPT.format(
            group_name=context.group_name,
            range_start=context.range_start,
            range_end=context.range_end,
            chunk_results=merged,
            weekday_hint=context.weekdays_text or "",
            total_messages=context.total_messages,
            speaker_count=context.speaker_count,
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        text = self._chat(messages)
        return ImagePromptResult(True, text.strip(), provider=self.name, model=self.model)

    def _chat(self, messages: list[dict]) -> str:
        base_url = self.settings.ai_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000,
        }
        last_error = ""
        for attempt in range(1, self.settings.ai_max_retries + 1):
            try:
                resp = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.ai_timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    logger.info("DeepSeek 调用成功（attempt %d）", attempt)
                    return content
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("DeepSeek attempt %d 失败：%s", attempt, last_error)
            except Exception as e:
                last_error = str(e)[:200]
                logger.warning("DeepSeek attempt %d 异常：%s", attempt, last_error)
            if attempt < self.settings.ai_max_retries:
                time.sleep(2 * attempt)
        raise RuntimeError(f"DeepSeek 调用失败：{last_error}")
