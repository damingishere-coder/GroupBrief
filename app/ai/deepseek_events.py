"""超长群聊片段的结构化事件卡解析与去重。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.conversation_segments import ConversationChunk

EVENT_ANALYZE_SYSTEM = """你是群聊事件分析助手。只能提取聊天中真实存在的事件、人物和原话。
必须返回一个 JSON 对象，不得输出 Markdown 或解释；聊天没有有效事件时返回 {"events": []}。"""

EVENT_ANALYZE_PROMPT = """以下是微信群聊记录片段（{label}，{start_time} 至 {end_time}）。

请返回：
{{
  "events": [
    {{
      "title": "事件短标题",
      "people": ["聊天中真实出现的人名"],
      "content": "基于聊天的事件描述",
      "quotes": ["1-3 条真实原话或忠实缩写"],
      "start_time": "事件开始时间",
      "end_time": "事件结束时间",
      "message_ids": ["支撑该事件的消息ID"]
    }}
  ]
}}

每片段最多 8 个事件。message_ids 必须来自记录中的【消息ID】，不得虚构；每个事件最多保留 30 条有效证据。
为避免响应截断，标题、描述、人物和引文必须简洁，输出必须是完整 JSON。

聊天记录：
{chunk_text}"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def build_event_prompt(chunk: ConversationChunk, label: str) -> str:
    return EVENT_ANALYZE_PROMPT.format(
        label=label,
        start_time=chunk.start_time,
        end_time=chunk.end_time,
        chunk_text=chunk.text,
    )


def _text(value: Any, *, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _text_list(value: Any, *, maximum_items: int, maximum_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:maximum_items]:
        text = _text(item, maximum=maximum_chars)
        if text and text not in result:
            result.append(text)
    return result


def parse_event_cards(raw: str, chunk: ConversationChunk) -> list[dict[str, Any]]:
    """解析并校验一个片段的事件 JSON；格式错误直接失败，禁止静默漏内容。"""
    cleaned = _FENCE_RE.sub("", (raw or "").strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"总结模型片段事件不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("总结模型片段事件缺少 events 数组")

    allowed_ids = set(chunk.message_ids)
    cards: list[dict[str, Any]] = []
    for raw_event in payload["events"][:8]:
        if not isinstance(raw_event, dict):
            raise ValueError("总结模型片段事件项必须是对象")
        title = _text(raw_event.get("title"), maximum=120)
        content = _text(raw_event.get("content"), maximum=1200)
        if not title or not content:
            raise ValueError("总结模型片段事件缺少 title/content")
        message_ids: list[str] = []
        for item in _text_list(raw_event.get("message_ids"), maximum_items=30, maximum_chars=160):
            # 超长单条消息会在提示词里显示为 id#片段N/M，证据仍归一到原消息。
            normalized_id = item.split("#片段", 1)[0]
            if normalized_id in allowed_ids and normalized_id not in message_ids:
                message_ids.append(normalized_id)
        if not message_ids:
            raise ValueError(f"总结模型片段事件“{title}”没有有效来源消息ID")
        cards.append(
            {
                "title": title,
                "people": _text_list(raw_event.get("people"), maximum_items=20, maximum_chars=80),
                "content": content,
                "quotes": _text_list(raw_event.get("quotes"), maximum_items=3, maximum_chars=300),
                "start_time": _text(raw_event.get("start_time"), maximum=40) or chunk.start_time,
                "end_time": _text(raw_event.get("end_time"), maximum=40) or chunk.end_time,
                "message_ids": list(dict.fromkeys(message_ids)),
            }
        )
    return cards


def deduplicate_event_cards(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """先消除重叠消息导致的重复卡；跨片段语义合并留给最终总结模型。"""
    seen_ids: set[str] = set()
    seen_fallback: set[str] = set()
    result: list[dict[str, Any]] = []
    for cards in groups:
        for card in cards:
            message_ids = [item for item in card.get("message_ids", []) if item not in seen_ids]
            fallback = re.sub(r"\s+", "", f"{card.get('title', '')}|{card.get('content', '')}").lower()
            if not message_ids and fallback in seen_fallback:
                continue
            if card.get("message_ids") and not message_ids:
                continue
            card = {**card, "message_ids": message_ids or card.get("message_ids", [])}
            seen_ids.update(card["message_ids"])
            seen_fallback.add(fallback)
            result.append(card)
    return result


def event_cards_json(cards: list[dict[str, Any]]) -> str:
    return json.dumps({"events": cards}, ensure_ascii=False, separators=(",", ":"))
