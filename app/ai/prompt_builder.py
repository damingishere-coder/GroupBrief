"""V2 生图 Prompt 构建器接口（P4 实现）。

输入：标准化聊天内容 + 群名 + 统计周期 + 消息数 + 发言人数
输出：image_prompt.txt（可直接交给 Codex `$imagegen` / GPT Image 2 使用）

模板：templates/image_prompt/（可编辑）
模型：固定 DeepSeek V4 Flash（复用 V1 app/providers/ai/deepseek.py）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PromptInput:
    """P4 ImagePromptBuilder 输入。"""

    group_name: str
    period_start: str
    period_end: str
    message_count: int
    speaker_count: int
    messages: list[Any]  # 标准化消息（含 ai_text）
    template: str = "default"


@dataclass
class PromptOutput:
    success: bool
    prompt: str = ""
    error: str = ""
    model: str = "deepseek-v4-flash"
    meta: dict[str, Any] | None = None  # 模型调用结构化元数据（不含 API Key）


class ImagePromptBuilder:
    """根据聊天内容生成最终生图 Prompt。P4 实现。"""

    name: str = "base"

    def build(self, data: PromptInput) -> PromptOutput:
        raise NotImplementedError
