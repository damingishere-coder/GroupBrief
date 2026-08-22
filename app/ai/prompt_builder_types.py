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
    report_date: str = ""
    template: str = "default"
    group_id: str = ""
    run_date: str = ""
    image_theme: str = "random_preset"
    image_theme_custom: str = ""
    template_override: str = ""
    previous_theme_signature: str = ""
    persisted_theme_meta: dict[str, Any] | None = None
    recent_layout_history: tuple[dict[str, Any], ...] = ()


@dataclass
class PromptOutput:
    success: bool
    prompt: str = ""
    error: str = ""
    model: str = "gpt-5.6-sol"
    meta: dict[str, Any] | None = None  # 模型调用结构化元数据（不含 API Key）
