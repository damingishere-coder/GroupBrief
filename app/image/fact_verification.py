"""严格群报图片的 OCR 与聊天证据一致性校验。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from app.ranking.policies import RANKING_POLICY_TEXT_PRIMARY
from app.ai.strict_prompt_contract import STRICT_IMAGE_FACT_MARKER


FACT_REVIEW_FILE = "image_fact_review.json"
_NUMERIC_FACT_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?:[^\S\r\n]*(?:%|％|kg|KG|公斤|斤|元|万元|万|W|w|天|℃|°C|岁|厘米|cm|米|m|小时|分钟))?"
)
_FACTUAL_TEXT_MARKERS = (
    "bmi",
    "体脂",
    "百分比",
    "温度",
    "天气",
    "雨",
    "连续",
    "饮食",
    "少油",
    "少盐",
    "健康",
    "自动发送",
)


@dataclass(frozen=True)
class ImageFactReview:
    ok: bool
    detail: str
    ocr_text: str = ""
    unknown_numeric: tuple[str, ...] = ()
    unknown_text: tuple[str, ...] = ()
    forbidden_facts: tuple[str, ...] = ()
    evidence_message_count: int = 0
    image_sha256: str = ""


def strict_fact_verification_enabled(prompt_file: Path) -> bool:
    run_path = prompt_file.with_name("run.json")
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(run, dict):
        return False
    return bool(
        run.get("image_fact_contract") == "strict_evidence_v1"
        or run.get("ranking_count_policy") == RANKING_POLICY_TEXT_PRIMARY
    )


def extract_image_text(image_path: Path) -> str:
    """复用 Windows 微信发送器的 WinRT OCR，只读取本地图片。"""
    from app.sender.wechat_native import WindowsWechatDriver

    lines = asyncio.run(WindowsWechatDriver._ocr_png(image_path.read_bytes()))
    return "\n".join(str(line.text or "").strip() for line in lines if line.text)


def _compact_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith(("P", "S", "Z", "C"))
    )


def _canonical_number(value: str) -> str:
    normalized = re.sub(
        r"\s+",
        "",
        value.replace("，", ".").replace(",", "."),
    ).casefold()
    return normalized.replace("万元", "w").replace("万", "w")


def _numeric_facts(value: str) -> set[str]:
    return {_canonical_number(match.group(0)) for match in _NUMERIC_FACT_RE.finditer(value)}


def _numeric_fact_is_allowed(candidate: str, allowed: set[str]) -> bool:
    if candidate in allowed:
        return True
    # 漫画 OCR 常把已知小数或三位数拆成 18/78/8万等片段。
    compact = re.sub(r"[.,，。\s]", "", candidate)
    if len(compact) >= 2:
        for expected in allowed:
            expected_compact = re.sub(r"[.,，。\s]", "", expected)
            if compact in expected_compact:
                return True
    return False


def _load_evidence(prompt_file: Path) -> tuple[list[str], str, int]:
    prompt = prompt_file.read_text(encoding="utf-8")
    visible_prompt = prompt.split(STRICT_IMAGE_FACT_MARKER, 1)[0]
    messages_path = prompt_file.with_name("messages.json")
    payload = json.loads(messages_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("messages.json 必须是非空数组")
    evidence_lines: list[str] = [
        line.strip() for line in visible_prompt.splitlines() if line.strip()
    ]
    numeric_evidence_lines: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        for field in ("group_name", "sender_name", "content"):
            text = str(item.get(field) or "").strip()
            if text:
                evidence_lines.append(text)
                numeric_evidence_lines.append(text)
    ranking_path = prompt_file.with_name("ranking.json")
    try:
        ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        ranking = {}
    if isinstance(ranking, dict):
        ranking_text = json.dumps(ranking, ensure_ascii=False)
        evidence_lines.append(ranking_text)
        numeric_evidence_lines.append(ranking_text)
    return evidence_lines, "\n".join(numeric_evidence_lines), len(payload)


def _forbidden_facts(ocr_text: str, message_evidence: str) -> tuple[str, ...]:
    compact = unicodedata.normalize("NFKC", ocr_text)
    findings: list[str] = []
    checks = (
        ("BMI", re.compile(r"bmi", re.IGNORECASE)),
        ("体脂率百分比", re.compile(r"体脂率[^\n]{0,16}(?:\d|%|％)")),
        ("体重身高计算式", re.compile(r"\d+(?:[.,]\d+)?\s*÷\s*\d+(?:[.,]\d+)?")),
        ("天气天数", re.compile(r"(?:雨|天气)[^\n]{0,16}\d+\s*天|\d+\s*天[^\n]{0,16}(?:雨|天气)")),
        ("温度", re.compile(r"\d+(?:[.,]\d+)?\s*(?:℃|°\s*[cC])")),
    )
    for label, pattern in checks:
        if pattern.search(compact):
            findings.append(label)
    if "少油少盐" in compact and "少油少盐" not in message_evidence:
        findings.append("无聊天依据的饮食改写")
    return tuple(findings)


def _matches_allowed_text(candidate: str, allowed: list[str], corpus: str) -> bool:
    compact = _compact_text(candidate)
    if len(compact) < 4:
        return True
    if compact in corpus:
        return True
    for expected in allowed:
        expected_compact = _compact_text(expected)
        if len(expected_compact) < 4:
            continue
        if compact in expected_compact or expected_compact in compact:
            return True
        length_ratio = min(len(compact), len(expected_compact)) / max(
            len(compact), len(expected_compact)
        )
        matcher = SequenceMatcher(None, compact, expected_compact)
        if length_ratio >= 0.45 and matcher.ratio() >= 0.6:
            return True
        longest = matcher.find_longest_match()
        if longest.size >= 4 and longest.size / len(compact) >= 0.6:
            return True
    return False


def _looks_like_factual_text(value: str) -> bool:
    compact = _compact_text(value)
    return any(marker in compact for marker in _FACTUAL_TEXT_MARKERS)


def review_image_facts(
    prompt_file: Path,
    image_path: Path,
    *,
    ocr_text: str | None = None,
) -> ImageFactReview:
    """把 OCR 文字与 Prompt/消息证据对照；严格模式下任何不明项均失败。"""
    try:
        evidence_lines, numeric_evidence, message_count = _load_evidence(prompt_file)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return ImageFactReview(False, f"图片事实校验缺少有效证据：{exc}")

    try:
        text = extract_image_text(image_path) if ocr_text is None else str(ocr_text)
    except Exception as exc:
        return ImageFactReview(False, f"图片事实 OCR 不可用：{type(exc).__name__}: {exc}")
    if not text.strip():
        return ImageFactReview(False, "图片事实 OCR 未识别到任何文字")

    allowed_blob = "\n".join(evidence_lines)
    allowed_numbers = _numeric_facts(numeric_evidence)
    # 分镜序号属于版式，不是聊天事实。
    allowed_numbers.update(str(number) for number in range(0, 11))
    unknown_numeric = tuple(
        sorted(
            (
                item
                for item in _numeric_facts(text)
                if not _numeric_fact_is_allowed(item, allowed_numbers)
            ),
            key=lambda item: (len(item), item),
        )
    )

    normalized_lines = [line.strip() for line in text.splitlines() if line.strip()]
    allowed_compact = _compact_text(allowed_blob)
    unknown_text = tuple(
        line[:120]
        for line in normalized_lines
        if _looks_like_factual_text(line)
        and not _matches_allowed_text(line, evidence_lines, allowed_compact)
    )
    forbidden_facts = _forbidden_facts(text, numeric_evidence)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if unknown_numeric or unknown_text or forbidden_facts:
        problems: list[str] = []
        if forbidden_facts:
            problems.append(f"禁止事实：{', '.join(forbidden_facts)}")
        if unknown_numeric:
            problems.append(f"无证据数字：{', '.join(unknown_numeric[:12])}")
        if unknown_text:
            problems.append(f"无证据文字：{'；'.join(unknown_text[:8])}")
        return ImageFactReview(
            False,
            "图片事实校验失败：" + "；".join(problems),
            text,
            unknown_numeric,
            unknown_text,
            forbidden_facts,
            message_count,
            digest,
        )
    return ImageFactReview(
        True,
        "图片 OCR 文案均可在 Prompt 或消息证据中找到",
        text,
        evidence_message_count=message_count,
        image_sha256=digest,
    )


def write_fact_review(prompt_file: Path, review: ImageFactReview) -> None:
    payload = {
        **asdict(review),
        "checked_at": datetime.now().astimezone().isoformat(),
        "ocr_text": review.ocr_text[:8000],
    }
    prompt_file.with_name(FACT_REVIEW_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
