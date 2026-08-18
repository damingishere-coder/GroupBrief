"""ImagePromptBuilder 数据结构（PromptInput / PromptOutput）。

独立于实现，供 P4 实现与后续 pipeline 引用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PromptInput:
    """ImagePromptBuilder 输入。"""

    group_name: str
    period_start: str
    period_end: str
    message_count: int
    speaker_count: int
    messages: list[Any]  # 标准化消息（含 timestamp/sender_name/content/message_type）
    template: str = "default"


@dataclass
class PromptOutput:
    success: bool
    prompt: str = ""
    error: str = ""
    model: str = "deepseek-v4-flash"
    meta: dict[str, Any] | None = None  # 模型调用结构化元数据（不含 API Key）
