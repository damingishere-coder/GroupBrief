"""群报首尾文字区的暗色遮挡筛查，不替代 OCR 事实核验或人工审图。

在中央文字区分块取亮度分位数，避免正常黑字本身触发；同时保留
高亮笔画的块，降低深色背景上白字的误报。只拦截大面积暗而缺少
高亮细节的区域，不声称能识别所有排版/缺字问题。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image
from app.image.normalization import opaque_report_image


READABILITY_REVIEW_FILE = "image_readability_review.json"


def _percentile(histogram: list[int], fraction: float) -> int:
    target = sum(histogram) * fraction
    cumulative = 0
    for value, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return value
    return 255


def review_image_readability(image_path: Path) -> dict:
    with Image.open(image_path) as image:
        gray = opaque_report_image(image).convert("L")
    width, height = gray.size
    regions = {}
    # 小图没有足够的文字区像素，不能套用海报的阈值。
    if width >= 256 and height >= 384:
        cell = max(8, width // 42)
        for name, top, bottom in (("顶部标题区", 0, 0.10), ("底部统计区", 0.92, 1)):
            dark = total = 0
            for y in range(int(height * top), int(height * bottom) - cell + 1, cell):
                for x in range(int(width * 0.15), int(width * 0.85) - cell + 1, cell):
                    histogram = gray.crop((x, y, x + cell, y + cell)).histogram()
                    dark += int(
                        _percentile(histogram, 0.80) < 140
                        and _percentile(histogram, 0.95) < 180
                    )
                    total += 1
            regions[name] = {"dark_tiles": dark, "total_tiles": total,
                             "dark_fraction": dark / total if total else 0}
    failed = [name for name, values in regions.items() if values["dark_fraction"] >= 0.35]
    return {
        "version": 1,
        "ok": not failed,
        "detail": (
            "、".join(failed) + "存在大面积暗色低对比区域，需重画或人工检查；禁止自动发送"
            if failed else "首尾文字区未发现大面积暗色低对比遮挡"
        ),
        "regions": regions,
        "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
    }


def verify_report_readability(prompt_file: Path, image_path: Path) -> tuple[bool, str]:
    """只对结构化群报启用；旧 Prompt 重画同样覆盖。"""
    prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.is_file() else ""
    if not all(marker in prompt for marker in ("【群名称】", "【统计时间】")):
        return True, ""
    review = review_image_readability(image_path)
    prompt_file.with_name(READABILITY_REVIEW_FILE).write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return review["ok"], review["detail"]
