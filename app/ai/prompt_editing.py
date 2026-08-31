"""群级/运行级生图 Prompt 编辑辅助。"""

from __future__ import annotations

import hashlib
import re

from app.ai.image_themes import ResolvedImageTheme

MAX_PROMPT_CHARS = 50_000
_THEME_SECTION_RE = re.compile(
    r"(?ms)^【(?:大主题|视觉风格)】\s*\n.*?(?=^【[^\n】]+】\s*$|\Z)"
)
_OVERALL_VISUAL_SECTION_RE = re.compile(
    r"(?ms)^【整体视觉】\s*\n(?P<body>.*?)(?=^【[^\n】]+】\s*$|\Z)"
)
_STYLE_LINE_RE = re.compile(
    r"(?m)^\s*(?:根据当天真实聊天内容自由选择统一视觉风格。|本次手动视觉风格：[^\n]*)\s*$"
)


def validate_prompt_text(
    value: object,
    *,
    expected_panel_count: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise ValueError("Prompt 必须是文本")
    text = value.strip()
    if not text:
        raise ValueError("Prompt 不能为空")
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f"Prompt 不能超过 {MAX_PROMPT_CHARS} 字")
    if "\x00" in text:
        raise ValueError("Prompt 不得包含空字符")
    from app.ai.poster_copy import validate_fixed_prompt_contract

    validate_fixed_prompt_contract(text, expected_panel_count=expected_panel_count)
    return text + "\n"


def prompt_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolved_theme_text(theme: ResolvedImageTheme) -> str:
    return theme.visible_text


def replace_theme_section(prompt: str, theme: ResolvedImageTheme) -> str:
    """新 Prompt 只替换【整体视觉】中的风格句；兼容历史独立主题段。"""
    overall = _OVERALL_VISUAL_SECTION_RE.search(prompt)
    if overall:
        body = _STYLE_LINE_RE.sub("", overall.group("body")).strip()
        style_line = (
            f"本次手动视觉风格：{resolved_theme_text(theme)}"
            if theme.has_explicit_style
            else "根据当天真实聊天内容自由选择统一视觉风格。"
        )
        replacement = "【整体视觉】\n" + body + "\n\n" + style_line + "\n\n"
        return (
            prompt[: overall.start()]
            + replacement
            + prompt[overall.end() :].lstrip()
        ).strip() + "\n"

    heading = "大主题" if theme.has_explicit_style else "视觉风格"
    block = f"【{heading}】\n{resolved_theme_text(theme)}\n\n"
    if _THEME_SECTION_RE.search(prompt):
        return _THEME_SECTION_RE.sub(block.rstrip(), prompt, count=1).strip() + "\n"
    return block + prompt.lstrip()
