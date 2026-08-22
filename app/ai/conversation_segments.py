"""按自然会话边界组织提交给群聊总结模型的聊天文本。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Iterable

DIRECT_CONTEXT_CHARS = 50_000
TARGET_CHUNK_CHARS = 32_000
HARD_CHUNK_CHARS = 50_000
SESSION_GAP_MINUTES = 20
OVERLAP_MESSAGES = 8


@dataclass(frozen=True)
class PromptMessage:
    message_id: str
    timestamp: datetime | None
    sender_name: str
    text: str
    sender_id: str = ""


@dataclass(frozen=True)
class ConversationChunk:
    text: str
    message_ids: tuple[str, ...]
    start_time: str
    end_time: str
    context_chars: int


@dataclass(frozen=True)
class _RenderedMessage:
    message_id: str
    timestamp: datetime | None
    rendered: str


def _timestamp_text(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "未知时间"


def _render(message: PromptMessage, suffix: str = "") -> str:
    message_id = message.message_id or "no-id"
    suffix_text = f"{suffix}" if suffix else ""
    return (
        f"[{_timestamp_text(message.timestamp)}][消息ID:{message_id}{suffix_text}] "
        f"{message.sender_name or '(未知)'}: {message.text}"
    )


def _split_oversized(message: PromptMessage, hard_chars: int) -> list[_RenderedMessage]:
    """超长单条优先按段落拆分，仍过长时才做硬字符切片。"""
    rendered = _render(message)
    if len(rendered) <= hard_chars:
        return [_RenderedMessage(message.message_id, message.timestamp, rendered)]

    prefix_budget = max(256, hard_chars - 160)
    paragraphs = [part.strip() for part in re.split(r"\n+", message.text) if part.strip()]
    if not paragraphs:
        paragraphs = [message.text]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidates = [paragraph[i : i + prefix_budget] for i in range(0, len(paragraph), prefix_budget)] or [""]
        for candidate in candidates:
            joined = f"{current}\n{candidate}".strip() if current else candidate
            if current and len(joined) > prefix_budget:
                pieces.append(current)
                current = candidate
            else:
                current = joined
    if current:
        pieces.append(current)

    total = len(pieces)
    return [
        _RenderedMessage(
            message.message_id,
            message.timestamp,
            _render(
                PromptMessage(message.message_id, message.timestamp, message.sender_name, piece),
                suffix=f"#片段{index}/{total}",
            ),
        )
        for index, piece in enumerate(pieces, start=1)
    ]


def _char_count(messages: Iterable[_RenderedMessage]) -> int:
    return sum(len(message.rendered) + 1 for message in messages)


def _to_chunk(messages: list[_RenderedMessage]) -> ConversationChunk:
    timestamps = [message.timestamp for message in messages if message.timestamp is not None]
    return ConversationChunk(
        text="\n".join(message.rendered for message in messages),
        message_ids=tuple(dict.fromkeys(message.message_id for message in messages)),
        start_time=_timestamp_text(min(timestamps)) if timestamps else "未知时间",
        end_time=_timestamp_text(max(timestamps)) if timestamps else "未知时间",
        context_chars=_char_count(messages),
    )


def segment_messages(
    messages: Iterable[PromptMessage],
    *,
    direct_chars: int = DIRECT_CONTEXT_CHARS,
    target_chars: int = TARGET_CHUNK_CHARS,
    hard_chars: int = HARD_CHUNK_CHARS,
    session_gap_minutes: int = SESSION_GAP_MINUTES,
    overlap_messages: int = OVERLAP_MESSAGES,
) -> list[ConversationChunk]:
    """典型群整群返回一个块；超长群按时间空档优先、字符预算兜底。"""
    direct_chars = max(1_000, int(direct_chars))
    hard_chars = max(direct_chars, int(hard_chars))
    target_chars = max(1_000, min(int(target_chars), hard_chars))
    overlap_messages = max(0, min(int(overlap_messages), 50))
    gap = timedelta(minutes=max(1, int(session_gap_minutes)))

    indexed = list(enumerate(messages))
    indexed.sort(key=lambda item: (item[1].timestamp or datetime.min, item[0]))
    rendered: list[_RenderedMessage] = []
    for _, message in indexed:
        text = (message.text or "").strip()
        if not text:
            continue
        rendered.extend(
            _split_oversized(
                PromptMessage(message.message_id, message.timestamp, message.sender_name, text),
                hard_chars,
            )
        )
    if not rendered:
        return []
    if _char_count(rendered) <= direct_chars:
        return [_to_chunk(rendered)]

    # 先按 20 分钟空档建立自然会话；无时间戳消息留在相邻会话中。
    sessions: list[list[_RenderedMessage]] = []
    current_session: list[_RenderedMessage] = []
    previous_ts: datetime | None = None
    for message in rendered:
        is_gap = (
            current_session
            and previous_ts is not None
            and message.timestamp is not None
            and message.timestamp - previous_ts >= gap
        )
        if is_gap:
            sessions.append(current_session)
            current_session = []
        current_session.append(message)
        if message.timestamp is not None:
            previous_ts = message.timestamp
    if current_session:
        sessions.append(current_session)

    chunks: list[list[_RenderedMessage]] = []
    current: list[_RenderedMessage] = []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = []

    for session in sessions:
        session_chars = _char_count(session)
        if session_chars <= target_chars:
            if current and _char_count(current) + session_chars > target_chars:
                flush()  # 在自然空档处分块，不需要上下文重叠。
            current.extend(session)
            continue

        # 单个连续会话本身过长，只能在字符预算处软切并携带尾部消息。
        if current:
            flush()
        for message in session:
            projected = _char_count(current) + len(message.rendered) + 1
            if current and projected > target_chars:
                previous = current
                flush()
                if overlap_messages:
                    current = previous[-overlap_messages:]
                while current and _char_count(current) + len(message.rendered) + 1 > hard_chars:
                    current.pop(0)
            current.append(message)
            if _char_count(current) >= hard_chars:
                flush()
        flush()
    flush()
    return [_to_chunk(chunk) for chunk in chunks if chunk]
