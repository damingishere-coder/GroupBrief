"""V2 图片生成任务：验证工具 + 受控并发调度器。

- verify_image：文件存在 / 大小 > 0 / 可被识别为常见图片格式（零依赖签名校验）；
- SerialImageQueue：兼容旧类名，默认串行并支持显式并发上限；
  单群失败不阻塞其他群（结果标记失败，继续下一群）；
- 每个群每天最多 1 张。
"""

from __future__ import annotations

import inspect
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
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


def verify_image_contract(prompt_file: Path, image_path: Path) -> tuple[bool, str]:
    """固定漫画 Prompt 声明 1024×1536 时，拒绝用裁切或错尺寸图片补救。"""
    ok, detail = verify_image(image_path)
    if not ok:
        return ok, detail
    try:
        prompt = prompt_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        prompt = ""
    if "1024×1536" not in prompt and "1024x1536" not in prompt.lower():
        return True, detail
    try:
        from PIL import Image

        with Image.open(image_path) as image:
            width, height = image.size
    except Exception as exc:
        return False, f"无法读取图片尺寸：{exc}"
    if (width, height) != (1024, 1536):
        return False, f"图片尺寸必须为 1024×1536，实际为 {width}×{height}；禁止裁切补救"
    return True, f"{detail}；尺寸 {width}×{height}"


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
        job_id: str = "",
        revision: int = 1,
        prompt_sha256: str = "",
    ):
        self.group_name = group_name
        self.prompt_file = prompt_file
        self.output_path = output_path
        self.generator = generator
        self.force = force
        self.job_id = job_id
        self.revision = max(1, int(revision))
        self.prompt_sha256 = prompt_sha256

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
            generate_parameters = inspect.signature(self.generator.generate).parameters
            if "job_id" in generate_parameters:
                result = self.generator.generate(
                    self.prompt_file,
                    self.output_path,
                    force=self.force,
                    job_id=self.job_id,
                    revision=self.revision,
                    prompt_sha256=self.prompt_sha256,
                )
            elif "force" in generate_parameters:
                result = self.generator.generate(
                    self.prompt_file,
                    self.output_path,
                    force=self.force,
                )
            else:
                # 保留第三方/测试 Generator 的两参数协议；正式 Codex 实现支持 force。
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
                "generator_detail": result.detail or {},
            }
        ok, detail = verify_image_contract(self.prompt_file, self.output_path)
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
            "generator_detail": result.detail or {},
        }


class SerialImageQueue:
    """兼容旧类名的受控图片队列；默认串行，可显式设置并发上限。"""

    def __init__(
        self,
        run_hook: Callable[[ImageJob, dict], None] | None = None,
        *,
        max_workers: int = 1,
    ):
        # run_hook：每群完成后回调（用于写 run.json / 日志）
        self.run_hook = run_hook
        self.max_workers = max(1, int(max_workers))

    @staticmethod
    def _run_timed(job: ImageJob) -> dict:
        started_at = perf_counter()
        result = job.run()
        result["imagegen_ms"] = round((perf_counter() - started_at) * 1000)
        return result

    def run_all(self, jobs: list[ImageJob]) -> list[dict]:
        """受控并发执行，结果顺序始终与输入任务一致。"""
        if not jobs:
            return []
        if self.max_workers == 1 or len(jobs) == 1:
            results: list[dict] = []
            for job in jobs:
                result = self._run_timed(job)
                results.append(result)
                if self.run_hook:
                    try:
                        self.run_hook(job, result)
                    except Exception:
                        pass
            return results

        results_by_index: dict[int, dict] = {}
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(jobs)),
            thread_name_prefix="groupbrief-image",
        ) as executor:
            futures = {
                executor.submit(self._run_timed, job): (index, job)
                for index, job in enumerate(jobs)
            }
            for future in as_completed(futures):
                index, job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "group_name": job.group_name,
                        "status": "failed",
                        "success": False,
                        "detail": str(exc)[:300],
                        "error_type": IMAGE_GENERATION_FAILED,
                        "imagegen_ms": 0,
                    }
                results_by_index[index] = result
                if self.run_hook:
                    try:
                        self.run_hook(job, result)
                    except Exception:
                        pass
        return [results_by_index[index] for index in range(len(jobs))]
