"""AI Prompt 生成 Provider 抽象。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.conversation_segments import PromptMessage


class ExternalCallError(RuntimeError):
    """外部 AI 调用失败的统一基类。"""


class ExternalCallNotSubmittedError(ExternalCallError):
    """可以确认请求尚未提交给 Provider。"""


class ExternalCallResultUnknownError(ExternalCallError):
    """请求可能已经被 Provider 接收，禁止自动重试或切换备用。"""


class ExternalCallInvalidResponseError(ExternalCallError):
    """Provider 已明确返回，但响应不可解析；禁止切备用，可安全本地降级。"""


@dataclass
class PromptContext:
    group_id: str
    group_name: str
    report_date: str
    range_start: str
    range_end: str
    total_messages: int
    speaker_count: int
    messages_text: str = ""  # 兼容旧调用；新链路优先使用 message_items
    message_items: list[PromptMessage] = field(default_factory=list)
    weekdays_text: str = ""  # 周一特殊标题提示等


@dataclass
class ImagePromptResult:
    success: bool
    prompt: str = ""
    error: str = ""
    provider: str = ""
    model: str = ""
    meta: dict = field(default_factory=dict)


class PromptGeneratorProvider:
    """根据群聊内容生成 GPT 生图 Prompt。"""

    name: str = "base"

    def build_context(
        self,
        group_id: str,
        group_name: str,
        report_date: str,
        range_start: str,
        range_end: str,
        total_messages: int,
        speaker_count: int,
        messages_text: str = "",
        message_items: list[PromptMessage] | None = None,
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
            message_items=list(message_items or []),
            weekdays_text=weekdays_text,
        )

    def generate_image_prompt(self, context: PromptContext) -> ImagePromptResult:
        raise NotImplementedError

    def health_check(self) -> tuple[bool, str]:
        raise NotImplementedError
