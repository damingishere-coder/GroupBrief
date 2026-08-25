"""群级/运行级生图 Prompt 编辑辅助。"""

from __future__ import annotations

import hashlib
import re

from app.ai.image_themes import ResolvedImageTheme

MAX_PROMPT_CHARS = 50_000
_THEME_SECTION_RE = re.compile(
    r"(?ms)^【(?:大主题|视觉风格)】\s*\n.*?(?=^【[^\n】]+】\s*$|\Z)"
)


def validate_prompt_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Prompt 必须是文本")
    text = value.strip()
    if not text:
        raise ValueError("Prompt 不能为空")
    if len(text) > MAX_PROMPT_CHARS:
        raise ValueError(f"Prompt 不能超过 {MAX_PROMPT_CHARS} 字")
    if "\x00" in text:
        raise ValueError("Prompt 不得包含空字符")
    return text + "\n"


def prompt_revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolved_theme_text(theme: ResolvedImageTheme) -> str:
    return theme.visible_text


def replace_theme_section(prompt: str, theme: ResolvedImageTheme) -> str:
    """只替换规范主题段；不存在时在开头插入，不改写其他内容。"""
    heading = "大主题" if theme.has_explicit_style else "视觉风格"
    block = f"【{heading}】\n{resolved_theme_text(theme)}\n\n"
    if _THEME_SECTION_RE.search(prompt):
        return _THEME_SECTION_RE.sub(block.rstrip(), prompt, count=1).strip() + "\n"
    return block + prompt.lstrip()
