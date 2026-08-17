"""AI Prompt 生成 Provider 抽象。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PromptContext:
    group_id: str
    group_name: str
    report_date: str
    range_start: str
    range_end: str
    total_messages: int
    speaker_count: int
    messages_text: str = ""  # 已整理、已截断的聊天文本
    weekdays_text: str = ""  # 周一特殊标题提示等


@dataclass
class ImagePromptResult:
    success: bool
    prompt: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""


class PromptGeneratorProvider:
    """根据群聊内容生成 GPT 生图 Prompt。"""

    name: str = "base"

    def generate_image_prompt(self, context: PromptContext) -> ImagePromptResult:
        raise NotImplementedError

    def health_check(self) -> tuple[bool, str]:
        raise NotImplementedError
