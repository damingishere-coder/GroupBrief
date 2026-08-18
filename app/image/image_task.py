"""V2 图片生成任务接口（P5 实现）。

读取 image_prompt.txt，通过 Codex `$imagegen` / GPT Image 2 生成图片，
可靠落盘到 output/{群名称}/{日期}/daily_image.png。
多群严格串行：当前群成功并确认文件存在后才开始下一个群。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GeneratedImage:
    """图片生成结果。"""

    path: Path
    size_bytes: int = 0
    status: str = "generated"  # generated / skipped / failed


@dataclass
class ImageTaskResult:
    success: bool
    image_path: Path | None = None
    error: str = ""
    detail: dict[str, Any] | None = None


class ImageGenerator:
    """串行图片生成器。P5 实现。"""

    name: str = "base"

    def health_check(self) -> tuple[bool, str]:
        raise NotImplementedError

    def generate(self, prompt_file: Path, output_path: Path) -> ImageTaskResult:
        """读取 prompt_file 生图并保存到 output_path。"""
        raise NotImplementedError
