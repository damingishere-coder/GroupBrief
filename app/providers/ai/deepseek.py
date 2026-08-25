"""DeepSeek V4 Flash Provider。

典型群聊整群一次提交；超长群聊按自然会话分段、并行提取结构化事件后
再合并。所有请求共享进程级并发上限，且固定关闭思考模式以降低日报延迟。
"""

from __future__ import annotations

import hashlib
import json
import random
import time

import httpx

from app.ai.concurrency import bounded_slot, normalized_limit, run_ai_tasks_ordered
from app.ai.conversation_segments import (
    HARD_CHUNK_CHARS,
    OVERLAP_MESSAGES,
    SESSION_GAP_MINUTES,
    TARGET_CHUNK_CHARS,
    ConversationChunk,
    PromptMessage,
    segment_messages,
)
from app.ai.deepseek_events import (
    EVENT_ANALYZE_SYSTEM,
    build_event_prompt,
    deduplicate_event_cards,
    event_cards_json,
    parse_event_cards,
)
from app.config.settings import Settings
from app.core.logging import get_logger
from app.providers.ai.base import (
    ExternalCallNotSubmittedError,
    ExternalCallResultUnknownError,
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
6. 数据必须使用给定数字，禁止自行计算。
7. 最终只选取 1～5 个最主要话题；内容不足时不硬凑。
8. 输出结构固定：
【任务】【群名称】【统计时间】【数据】【主标题】【副标题】【整体视觉】
【版面1】～【版面N】【底部总结】【硬性要求】"""

DIRECT_PROMPT = """以下是微信群「{group_name}」在 {range_start} ~ {range_end} 的完整可统计聊天记录：

{messages_text}

请基于完整上下文直接生成漫画日报海报 Prompt。
{weekday_hint}
必须遵守：不编造、不遗漏主要连续事件；数据（{total_messages} 条消息、{speaker_count} 人发言）必须原样使用。"""

MERGE_PROMPT = """你已获得微信群「{group_name}」在 {range_start} ~ {range_end} 的结构化事件卡：

{event_cards}

请合并语义相同的事件，保留跨片段的连续过程和真实细节，然后生成完整漫画日报海报 Prompt。
{weekday_hint}
必须遵守：不编造、不添加事件卡中不存在的内容；数据（{total_messages} 条消息、{speaker_count} 人发言）必须原样使用；最终只选 1～5 个主要话题。"""


class DeepSeekV4FlashProvider(PromptGeneratorProvider):
    name = "deepseek"
    model = "deepseek-v4-flash"

    def __init__(self, settings: Settings):
        self.settings = settings
        configured = (settings.ai_model or self.model).strip()
        self.model = self.model if configured in {"", "deepseek-chat"} else configured

    def health_check(self) -> tuple[bool, str]:
        if not self.settings.ai_api_key:
            return False, "未配置 AI_API_KEY"
        return True, f"已配置（{self.model}，非思考模式）"

    def _messages(self, context: PromptContext) -> list[PromptMessage]:
        if context.message_items:
            return list(context.message_items)
        return [
            PromptMessage(f"legacy-{index}", None, "", line)
            for index, line in enumerate(context.messages_text.splitlines(), start=1)
            if line.strip()
        ]

    def _segment(self, messages: list[PromptMessage]) -> list[ConversationChunk]:
        direct_chars = max(1_000, int(self.settings.max_context_chars or 50_000))
        return segment_messages(
            messages,
            direct_chars=direct_chars,
            target_chars=min(TARGET_CHUNK_CHARS, direct_chars),
            hard_chars=max(HARD_CHUNK_CHARS, direct_chars),
            session_gap_minutes=SESSION_GAP_MINUTES,
            overlap_messages=OVERLAP_MESSAGES,
        )

    def generate_image_prompt(self, context: PromptContext) -> ImagePromptResult:
        try:
            source_messages = self._messages(context)
            chunks = self._segment(source_messages)
            if not chunks:
                raise ValueError("没有可提交给总结模型的聊天文本")
            context_chars = sum(len(message.text) for message in source_messages)
            logger.info(
                "群 %s：共 %d 行、%d 字，按自然边界分 %d 块",
                context.group_name,
                len(source_messages),
                context_chars,
                len(chunks),
            )

            if len(chunks) == 1:
                prompt = DIRECT_PROMPT.format(
                    group_name=context.group_name,
                    range_start=context.range_start,
                    range_end=context.range_end,
                    messages_text=chunks[0].text,
                    weekday_hint=context.weekdays_text or "",
                    total_messages=context.total_messages,
                    speaker_count=context.speaker_count,
                )
                text = self._chat(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ]
                )
                return ImagePromptResult(
                    True,
                    text.strip(),
                    provider=self.name,
                    model=self.model,
                    meta={"mode": "direct", "chunk_count": 1, "api_call_count": 1, "context_chars": context_chars},
                )

            indexed_chunks = list(enumerate(chunks, start=1))

            def analyze(item: tuple[int, ConversationChunk]) -> list[dict]:
                index, chunk = item
                raw = self._chat(
                    [
                        {"role": "system", "content": EVENT_ANALYZE_SYSTEM},
                        {"role": "user", "content": build_event_prompt(chunk, f"第 {index}/{len(chunks)} 块")},
                    ],
                    response_format="json_object",
                    temperature=0.1,
                    max_tokens=3000,
                )
                return parse_event_cards(raw, chunk)

            analyses = run_ai_tasks_ordered(
                analyze,
                indexed_chunks,
                max_workers=normalized_limit(self.settings.ai_request_concurrency, 6),
            )
            cards = deduplicate_event_cards(analyses)
            if not cards:
                raise ValueError("总结模型未从超长聊天中提取到可验证事件")
            prompt = MERGE_PROMPT.format(
                group_name=context.group_name,
                range_start=context.range_start,
                range_end=context.range_end,
                event_cards=event_cards_json(cards),
                weekday_hint=context.weekdays_text or "",
                total_messages=context.total_messages,
                speaker_count=context.speaker_count,
            )
            text = self._chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            )
            return ImagePromptResult(
                True,
                text.strip(),
                provider=self.name,
                model=self.model,
                meta={
                    "mode": "natural_chunked",
                    "chunk_count": len(chunks),
                    "event_count": len(cards),
                    "api_call_count": len(chunks) + 1,
                    "context_chars": context_chars,
                },
            )
        except Exception as exc:
            logger.exception("群聊总结模型调用失败")
            return ImagePromptResult(False, error=str(exc)[:300], provider=self.name, model=self.model)

    # 兼容旧测试/诊断调用；返回值已改为自然分段块。
    def _chunk_messages(self, lines: list[str]) -> list[ConversationChunk]:
        messages = [PromptMessage(f"legacy-{index}", None, "", line) for index, line in enumerate(lines, start=1)]
        return self._segment(messages)

    def _chat(
        self,
        messages: list[dict],
        *,
        response_format: str = "text",
        temperature: float = 0.7,
        max_tokens: int = 3000,
    ) -> str:
        base_url = self.settings.ai_base_url.rstrip("/")
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "thinking": {"type": "disabled"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        request_id = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        attempts = max(1, int(self.settings.ai_max_retries))
        last_error = ""
        last_failure_was_pre_submit = False
        for attempt in range(1, attempts + 1):
            try:
                with bounded_slot(
                    "deepseek_request",
                    normalized_limit(self.settings.ai_request_concurrency, 6),
                ):
                    response = httpx.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=self.settings.ai_timeout_seconds,
                    )
            except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ConnectError) as exc:
                # 连接尚未建立或尚未取得连接池槽位，可以确认未提交。
                last_error = str(exc)[:160] or exc.__class__.__name__
                last_failure_was_pre_submit = True
                logger.warning(
                    "DeepSeek 连接前失败（attempt=%d request_id=%s）：%s",
                    attempt,
                    request_id,
                    last_error,
                )
                if attempt < attempts:
                    delay = min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
                    time.sleep(delay)
                    continue
                break
            except httpx.InvalidURL as exc:
                raise ExternalCallNotSubmittedError(
                    f"DeepSeek URL 无效（request_id={request_id}）：{str(exc)[:160]}"
                ) from exc
            except httpx.RequestError as exc:
                # 写入超时、读取超时、远端中断都无法证明请求未到达 Provider。
                raise ExternalCallResultUnknownError(
                    f"DeepSeek 请求结果未知（request_id={request_id}）：{exc.__class__.__name__}"
                ) from exc

            if response.status_code == 200:
                try:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("DeepSeek 返回空内容")
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ExternalCallResultUnknownError(
                        f"DeepSeek 成功响应无法解析（request_id={request_id}）"
                    ) from exc
                logger.info(
                    "DeepSeek 调用成功（attempt=%d request_id=%s）", attempt, request_id
                )
                return content

            # 429/503 明确表示限流或暂不可用，可以受控重试；其他 5xx 不能
            # 证明 Provider 没有在内部完成请求，按结果未知处理。
            last_failure_was_pre_submit = False
            last_error = f"HTTP {response.status_code}"
            if response.status_code >= 500 and response.status_code != 503:
                raise ExternalCallResultUnknownError(
                    f"DeepSeek 服务端错误且结果未知（request_id={request_id}）：{last_error}"
                )
            retryable = response.status_code in {429, 503}
            logger.warning(
                "DeepSeek 明确拒绝（attempt=%d request_id=%s）：%s",
                attempt,
                request_id,
                last_error,
            )
            if attempt >= attempts or not retryable:
                break
            delay = min(8.0, 2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
            time.sleep(delay)
        if last_failure_was_pre_submit:
            raise ExternalCallNotSubmittedError(
                f"DeepSeek 未提交：{last_error}（request_id={request_id}）"
            )
        raise RuntimeError(f"DeepSeek 调用失败：{last_error}")
