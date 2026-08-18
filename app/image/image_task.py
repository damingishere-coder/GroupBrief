"""V2 图片生成任务：验证工具 + 串行调度器。

- verify_image：文件存在 / 大小 > 0 / 可被识别为常见图片格式（零依赖签名校验）；
- SerialImageQueue：多群图片严格串行——当前群生成成功并确认文件存在，
  才允许开始下一个群；单群失败不阻塞其他群（结果标记失败，继续下一群）；
- 每个群每天最多 1 张。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.v2.constants import (
    IMAGE_GENERATION_FAILED,
    IMAGE_FILE_MISSING,
)


@dataclass
class GeneratedImage:
    """图片生成结果（P0 接口约定）。"""

    path: Path
    size_bytes: int = 0
    status: str = "generated"  # generated / skipped / failed


@dataclass
class ImageTaskResult:
    """生图任务结果（P0 接口约定）。"""

    success: bool
    image_path: Path | None = None
    error: str = ""
    detail: dict[str, Any] | None = None

# 常见图片格式的魔数签名
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"BM", "bmp"),
)


def detect_image_format(path: Path) -> str | None:
    """识别图片格式；不是已知图片格式返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return None
    for signature, fmt in _IMAGE_SIGNATURES:
        if head.startswith(signature):
            return fmt
    return None


def verify_image(path: Path) -> tuple[bool, str]:
    """验证生成图片：存在 / 大小>0 / 可解析为图片。"""
    if not path.exists():
        return False, f"图片文件不存在：{path}"
    size = path.stat().st_size
    if size <= 0:
        return False, f"图片文件为空（{size} 字节）：{path}"
    fmt = detect_image_format(path)
    if fmt is None:
        return False, f"文件不是可识别的图片格式：{path}"
    return True, f"OK：{fmt} 图片，{size} 字节"


def copy_generated_image(src: Path, dst: Path) -> None:
    """把生成图片复制/移动到目标路径（output/<群>/<日期>/daily_image.png）。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


class ImageJob:
    """一次生图任务。"""

    def __init__(
        self,
        group_name: str,
        prompt_file: Path,
        output_path: Path,
        generator: Any,
        force: bool = False,
    ):
        self.group_name = group_name
        self.prompt_file = prompt_file
        self.output_path = output_path
        self.generator = generator
        self.force = force

    def run(self) -> dict:
        """执行生图并验证落盘。返回结构化结果。"""
        # 已存在有效图片且非 force：跳过，不重复生成
        if not self.force:
            ok, _ = verify_image(self.output_path)
            if ok:
                return {
                    "group_name": self.group_name,
                    "status": "skipped",
                    "success": True,
                    "detail": "图片已存在，跳过生成",
                    "error_type": "",
                }
        try:
            result = self.generator.generate(self.prompt_file, self.output_path)
        except Exception as e:  # 生成器内部异常
            return {
                "group_name": self.group_name,
                "status": "failed",
                "success": False,
                "detail": str(e)[:300],
                "error_type": IMAGE_GENERATION_FAILED,
            }
        if not result.success:
            return {
                "group_name": self.group_name,
                "status": "failed",
                "success": False,
                "detail": result.error,
                "error_type": IMAGE_GENERATION_FAILED,
            }
        ok, detail = verify_image(self.output_path)
        if not ok:
            return {
                "group_name": self.group_name,
                "status": "failed",
                "success": False,
                "detail": f"生成后验证失败：{detail}",
                "error_type": IMAGE_FILE_MISSING,
            }
        return {
            "group_name": self.group_name,
            "status": "success",
            "success": True,
            "detail": f"图片已落盘：{self.output_path}",
            "error_type": "",
        }


class SerialImageQueue:
    """严格串行生图队列（多群共用单队列）。"""

    def __init__(self, run_hook: Callable[[ImageJob, dict], None] | None = None):
        # run_hook：每群完成后回调（用于写 run.json / 日志）
        self.run_hook = run_hook

    def run_all(self, jobs: list[ImageJob]) -> list[dict]:
        """按顺序逐群执行；上一群完成（成功或失败）后才执行下一群。"""
        results: list[dict] = []
        for job in jobs:
            result = job.run()
            results.append(result)
            if self.run_hook:
                try:
                    self.run_hook(job, result)
                except Exception:
                    pass
        return results
