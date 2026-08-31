"""最终生图 Prompt 的确定性清洗与长度预算。"""

from __future__ import annotations

import re
import unicodedata

_SECTION_RE = re.compile(r"(?m)^(【[^\n】]{1,80}】[^\n]*)$")


def sanitize_prompt_text(value: object, *, allow_newlines: bool = True) -> tuple[str, int]:
    """移除可能破坏文件/模型边界的控制字符，并统一换行。"""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    removed = 0
    output: list[str] = []
    for char in text:
        if char == "\n" and allow_newlines:
            output.append(char)
            continue
        if char == "\t":
            output.append(" ")
            continue
        if unicodedata.category(char) in {"Cc", "Cf", "Cs"}:
            removed += 1
            continue
        output.append(char)
    cleaned = "".join(output)
    if allow_newlines:
        cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    else:
        cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(), removed


def _truncate_utf8(text: str, max_bytes: int) -> str:
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    return text.encode("utf-8")[: max(max_bytes, 0)].decode("utf-8", errors="ignore")


def _clip_middle(text: str, char_budget: int) -> str:
    if len(text) <= char_budget:
        return text
    marker = "\n…（超长内容已确定性压缩）…\n"
    if char_budget <= len(marker) + 20:
        return text[:char_budget]
    remaining = char_budget - len(marker)
    head = max(1, remaining * 2 // 3)
    return text[:head] + marker + text[-(remaining - head):]


def enforce_prompt_budget(
    value: object,
    *,
    max_chars: int,
    max_bytes: int,
) -> tuple[str, dict[str, int | bool]]:
    """保留每个 ``【章节】`` 标题及章节首尾，把最终 Prompt 限定在硬预算内。"""
    cleaned, removed = sanitize_prompt_text(value)
    char_limit = max(1_000, int(max_chars))
    byte_limit = max(4_000, int(max_bytes))
    original_chars = len(cleaned)
    original_bytes = len(cleaned.encode("utf-8"))
    compacted = original_chars > char_limit or original_bytes > byte_limit
    result = cleaned

    if compacted:
        matches = list(_SECTION_RE.finditer(cleaned))
        if matches:
            preamble = cleaned[: matches[0].start()].strip()
            sections: list[tuple[str, str]] = []
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
                sections.append((match.group(1).strip(), cleaned[match.end():end].strip()))
            fixed = sum(len(title) + 2 for title, _ in sections) + len(preamble)
            content_budget = max(char_limit - fixed - 64, len(sections) * 24)
            per_section = max(24, content_budget // max(len(sections), 1))
            parts = [preamble] if preamble else []
            for title, body in sections:
                parts.append(title)
                if body:
                    parts.append(_clip_middle(body, per_section))
            result = "\n".join(parts)
        result = _clip_middle(result, char_limit)
        result = _truncate_utf8(result, byte_limit)
        result = result.rstrip()

    return result, {
        "prompt_controls_removed": removed,
        "prompt_compacted": compacted,
        "prompt_original_chars": original_chars,
        "prompt_original_bytes": original_bytes,
        "prompt_final_chars": len(result),
        "prompt_final_bytes": len(result.encode("utf-8")),
    }
