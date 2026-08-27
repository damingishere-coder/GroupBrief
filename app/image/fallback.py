"""图片生成的确定性安全化 Prompt 与 Pillow Level 3 信息图。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont


POLICY_ERROR_CODES = frozenset(
    {"POLICY_REJECTED", "CONTENT_FILTER", "SAFETY_FILTER", "PROMPT_BLOCKED"}
)

_SENSITIVE_TERMS = (
    "自杀",
    "血腥",
    "裸露",
    "色情",
    "毒品",
    "武器制作",
    "仇恨言论",
)


def _clean_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    cleaned: list[str] = []
    for char in text:
        if char in "\r\n\t":
            cleaned.append(" ")
            continue
        category = unicodedata.category(char)
        if category in {"Cc", "Cf"}:
            continue
        cleaned.append(char)
    return re.sub(r"[ ]{2,}", " ", "".join(cleaned)).strip()


def image_failure_code(detail: Mapping[str, Any] | None) -> str:
    detail = detail if isinstance(detail, Mapping) else {}
    for key in ("error_type", "error_code", "code", "status"):
        value = str(detail.get(key) or "").strip().upper()
        if value in POLICY_ERROR_CODES:
            return value
    return ""


def image_result_is_unknown(detail: Mapping[str, Any] | None) -> bool:
    detail = detail if isinstance(detail, Mapping) else {}
    return bool(
        detail.get("outcome_unknown")
        or str(detail.get("recovery_status") or "").lower()
        in {"result_unknown", "timeout_process_still_running"}
        or str(detail.get("stage") or "").lower() == "ambiguous"
    )


def sanitize_image_prompt(
    prompt: str,
    *,
    group_name: str,
    ranking: Mapping[str, Any] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """只泛化身份和风险表达，不重新选题，也不改变数字事实。"""
    safe = _clean_text(prompt)
    redactions: list[dict[str, str]] = []
    clean_group = _clean_text(group_name)
    if clean_group and clean_group in safe:
        safe = safe.replace(clean_group, "今日群聊")
        redactions.append({"type": "group_name", "replacement": "今日群聊"})

    speakers = []
    if isinstance(ranking, Mapping):
        rows = ranking.get("top_speakers")
        if isinstance(rows, list):
            speakers = [
                _clean_text(row.get("name"))
                for row in rows
                if isinstance(row, Mapping) and _clean_text(row.get("name"))
            ]
    for index, name in enumerate(dict.fromkeys(speakers)):
        alias = f"群友{chr(ord('A') + min(index, 25))}"
        if name in safe:
            safe = safe.replace(name, alias)
            redactions.append({"type": "nickname", "replacement": alias})

    for term in _SENSITIVE_TERMS:
        if term in safe:
            safe = safe.replace(term, "相关风险表达")
            redactions.append({"type": "sensitive_expression", "replacement": "相关风险表达"})

    header = (
        "【安全化版本】仅泛化昵称和可能触发审核的表达；必须保留原日期、"
        "数字事实、话题顺序、主要话题与排行榜，不新增人物或事实。\n"
    )
    return header + safe, redactions


def _font_candidates(explicit: str = "") -> list[Path]:
    paths: list[Path] = []
    if explicit:
        paths.append(Path(explicit))
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    paths.extend(
        [
            windows / "msyhbd.ttc",
            windows / "msyh.ttc",
            windows / "simhei.ttf",
            windows / "simsun.ttc",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    )
    return paths


def _load_font(size: int, explicit: str = "") -> tuple[ImageFont.ImageFont, str]:
    for path in _font_candidates(explicit):
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=size), str(path)
        except OSError:
            continue
    return ImageFont.load_default(), "Pillow/default"


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int, limit: int) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(current)
            current = char
            if len(lines) >= limit:
                break
        else:
            current = candidate
    if current and len(lines) < limit:
        lines.append(current)
    if len(lines) == limit and sum(len(line) for line in lines) < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def render_local_infographic(
    *,
    group_name: str,
    run_date: str,
    ranking_path: Path,
    run_path: Path,
    output_path: Path,
    font_path: str = "",
    failure_class: str = "IMAGE_GENERATION_FAILED",
) -> dict[str, Any]:
    """从当天结构化产物渲染固定 1024×1536 PNG，并原子提升。"""
    ranking = _load_json(ranking_path)
    run = _load_json(run_path)
    canvas = Image.new("RGB", (1024, 1536), "#F4F7FB")
    draw = ImageDraw.Draw(canvas)
    title_font, used_font = _load_font(52, font_path)
    section_font, _ = _load_font(34, font_path)
    body_font, _ = _load_font(28, font_path)
    small_font, _ = _load_font(22, font_path)

    draw.rounded_rectangle((56, 48, 968, 238), radius=28, fill="#173B57")
    title = _clean_text(group_name) or "今日群聊"
    draw.text((92, 82), title[:22], font=title_font, fill="white")
    draw.text((92, 160), f"{run_date} · 简化版数据卡片", font=body_font, fill="#D7EAF7")

    message_count = int(ranking.get("message_count") or run.get("message_count") or 0)
    speaker_count = int(ranking.get("speaker_count") or run.get("speaker_count") or 0)
    draw.rounded_rectangle((56, 270, 968, 410), radius=24, fill="white")
    draw.text((92, 294), f"消息 {message_count}", font=section_font, fill="#173B57")
    draw.text((520, 294), f"参与 {speaker_count}", font=section_font, fill="#173B57")
    draw.text((92, 355), "外部生图不可用，已自动生成本地信息图", font=small_font, fill="#63778A")

    draw.text((72, 458), "活跃排行", font=section_font, fill="#173B57")
    speakers = ranking.get("top_speakers") if isinstance(ranking.get("top_speakers"), list) else []
    max_count = max([int(row.get("count") or 0) for row in speakers if isinstance(row, Mapping)] or [1])
    y = 520
    for index, row in enumerate(speakers[:8]):
        if not isinstance(row, Mapping):
            continue
        name = _clean_text(row.get("name")) or f"群友{index + 1}"
        count = int(row.get("count") or 0)
        draw.text((84, y), f"{index + 1}. {name[:12]}", font=body_font, fill="#263746")
        bar_width = max(12, int(360 * count / max_count))
        draw.rounded_rectangle((480, y + 5, 480 + bar_width, y + 35), radius=14, fill="#55A7D9")
        draw.text((864, y), str(count), font=body_font, fill="#263746")
        y += 68

    prompt_meta = run.get("prompt_meta") if isinstance(run.get("prompt_meta"), Mapping) else {}
    selection = prompt_meta.get("topic_selection") if isinstance(prompt_meta.get("topic_selection"), Mapping) else {}
    candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
    draw.text((72, 1088), "主要话题", font=section_font, fill="#173B57")
    y = 1146
    for row in [item for item in candidates if isinstance(item, Mapping) and item.get("selected")][:3]:
        title = _clean_text(row.get("title")) or "当天主要话题"
        summary = _clean_text(row.get("summary"))
        for line in _fit_lines(draw, f"• {title}：{summary}", body_font, 840, 2):
            draw.text((88, y), line, font=body_font, fill="#263746")
            y += 40
        y += 16

    draw.text(
        (72, 1464),
        f"fallback=L3 · reason={_clean_text(failure_class)[:40]}",
        font=small_font,
        fill="#7B8792",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp.png")
    canvas.save(temp_path, format="PNG", optimize=True)
    with Image.open(temp_path) as check:
        check.load()
        if check.size != (1024, 1536):
            raise ValueError(f"本地信息图尺寸异常：{check.size}")
    os.replace(temp_path, output_path)
    return {
        "fallback_level": 3,
        "image_variant": "pillow",
        "fallback_reason": failure_class,
        "fallback_font": used_font,
        "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
    }
