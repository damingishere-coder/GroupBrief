"""V2 图片生成任务：验证工具 + 受控并发调度器。

- verify_image：文件存在 / 大小 > 0 / 可被识别为常见图片格式（零依赖签名校验）；
- SerialImageQueue：兼容旧类名，默认串行并支持显式并发上限；
  单群失败不阻塞其他群（结果标记失败，继续下一群）；
- 每个群每天最多 1 张。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.v2.constants import (
    IMAGE_CONTENT_VERIFICATION_FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_FILE_MISSING,
    PROMPT_FAILED,
)
from app.image.delivery_guard import image_delivery_eligible
from app.image.fallback import (
    image_failure_code,
    image_result_is_unknown,
    sanitize_image_prompt,
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
    """统一强校验：完整解码、格式与扩展名一致、尺寸非零。"""
    if not path.exists():
        return False, f"图片文件不存在：{path}"
    size = path.stat().st_size
    if size <= 0:
        return False, f"图片文件为空（{size} 字节）：{path}"
    if size > 50 * 1024 * 1024:
        return False, f"图片文件超过 50MiB：{path}"
    try:
        from PIL import Image

        with Image.open(path) as image:
            detected = str(image.format or "").lower()
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except Exception as exc:
        return False, f"图片无法完整解码：{exc}"
    if width <= 0 or height <= 0:
        return False, f"图片尺寸无效：{width}×{height}"
    suffix = path.suffix.lower()
    expected_formats = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".gif": "gif", ".webp": "webp", ".tif": "tiff", ".tiff": "tiff", ".bmp": "bmp"}
    expected = expected_formats.get(suffix)
    if expected and detected != expected:
        return False, f"图片格式与扩展名不一致：{detected}/{suffix}"
    return True, f"OK：{detected} 图片，{size} 字节，尺寸 {width}×{height}"


def verify_image_contract(prompt_file: Path, image_path: Path) -> tuple[bool, str]:
    """校验群报图片完整性；Prompt 中的画布尺寸仅作为生成偏好。"""
    ok, detail = verify_image(image_path)
    if not ok:
        return ok, detail
    from app.image.fact_verification import (
        review_image_facts,
        strict_fact_verification_enabled,
        write_fact_review,
    )

    if not strict_fact_verification_enabled(prompt_file):
        return True, detail
    review = review_image_facts(prompt_file, image_path)
    write_fact_review(prompt_file, review)
    if not review.ok:
        return False, review.detail
    return True, f"{detail}；{review.detail}"


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

    def _call_generator(
        self,
        prompt_file: Path,
        *,
        safe_variant: bool = False,
        quality_retry: bool = False,
    ):
        generate_parameters = inspect.signature(self.generator.generate).parameters
        prompt_sha256 = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
        if safe_variant and self.job_id:
            job_id = f"{self.job_id}-safe"
        elif quality_retry and self.job_id:
            job_id = f"{self.job_id}-quality-2"
        else:
            job_id = self.job_id
        if "job_id" in generate_parameters:
            return self.generator.generate(
                prompt_file,
                self.output_path,
                force=self.force,
                job_id=job_id,
                revision=self.revision + (1 if safe_variant or quality_retry else 0),
                prompt_sha256=prompt_sha256,
            )
        if "force" in generate_parameters:
            return self.generator.generate(prompt_file, self.output_path, force=self.force)
        return self.generator.generate(prompt_file, self.output_path)

    def _local_fallback(self, failure_class: str) -> dict:
        """兼容旧调用点：失败时清理目标文件，不再生成统计信息图。"""

        if self.output_path.exists():
            self.output_path.unlink()
        error_type = (
            failure_class
            if failure_class
            in {
                PROMPT_FAILED,
                IMAGE_CONTENT_VERIFICATION_FAILED,
                IMAGE_FILE_MISSING,
            }
            else IMAGE_GENERATION_FAILED
        )
        return {
            "group_name": self.group_name,
            "status": "failed",
            "success": False,
            "detail": "正常 AI 生图失败；统计表兜底已停用，本次不生成图片",
            "error_type": error_type,
            "generator_detail": {
                "fallback_level": 0,
                "image_variant": "normal",
                "local_infographic_disabled": True,
            },
        }

    def run(self) -> dict:
        """执行生图并验证落盘。返回结构化结果。"""
        try:
            run_state = json.loads(
                (self.output_path.parent / "run.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            run_state = {}
        # 已存在的 Level 3/Pillow 文件虽然是合法 PNG，但只是诊断产物，不能
        # 被重试流程误认成真实成功。只有来源可发送的旧图才允许跳过。
        if not self.force and image_delivery_eligible(run_state):
            ok, _ = verify_image_contract(self.prompt_file, self.output_path)
            if ok:
                return {
                    "group_name": self.group_name,
                    "status": "skipped",
                    "success": True,
                    "detail": "图片已存在，跳过生成",
                    "error_type": "",
                }
        elif not self.force and self.output_path.exists():
            # 旧 Level 3/Pillow 诊断图不能继续留在默认输出路径，否则底层
            # 生成器只看到“可解码 PNG”就会走 existing_output_reused。
            try:
                self.output_path.unlink()
            except OSError as exc:
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": f"旧诊断图无法清理，已停止生图：{str(exc)[:160]}",
                    "error_type": IMAGE_GENERATION_FAILED,
                }
        if isinstance(run_state, dict) and run_state.get("image_force_local_fallback"):
            try:
                return self._local_fallback(
                    str(run_state.get("prompt_fallback_reason") or PROMPT_FAILED)
                )
            except Exception as fallback_exc:
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": f"本地信息图生成失败：{str(fallback_exc)[:240]}",
                    "error_type": IMAGE_GENERATION_FAILED,
                }
        try:
            result = self._call_generator(self.prompt_file)
        except Exception as e:  # 生成器内部异常
            try:
                return self._local_fallback(type(e).__name__)
            except Exception as fallback_exc:
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": f"{str(e)[:180]}；本地兜底失败：{str(fallback_exc)[:100]}",
                    "error_type": IMAGE_GENERATION_FAILED,
                }
        if not result.success:
            generator_detail = result.detail or {}
            if image_result_is_unknown(generator_detail):
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": result.error,
                    "error_type": IMAGE_GENERATION_FAILED,
                    "generator_detail": generator_detail,
                }
            policy_code = image_failure_code(generator_detail)
            safe_failure_detail = ""
            if policy_code:
                try:
                    ranking_path = self.output_path.parent / "ranking.json"
                    try:
                        ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        ranking = {}
                    safe_prompt, redactions = sanitize_image_prompt(
                        self.prompt_file.read_text(encoding="utf-8"),
                        group_name=self.group_name,
                        ranking=ranking,
                    )
                    safe_path = self.prompt_file.with_name("image_prompt.safe.txt")
                    safe_path.write_text(safe_prompt, encoding="utf-8")
                    safe_result = self._call_generator(safe_path, safe_variant=True)
                    if safe_result.success:
                        ok, detail = verify_image_contract(safe_path, self.output_path)
                        if ok:
                            safe_detail = dict(safe_result.detail or {})
                            safe_detail.update(
                                fallback_level=2,
                                image_variant="safe",
                                safety_error_code=policy_code,
                                safety_redactions=redactions,
                            )
                            return {
                                "group_name": self.group_name,
                                "status": "success",
                                "success": True,
                                "detail": f"安全化 Prompt 生图成功：{detail}",
                                "error_type": "",
                                "generator_detail": safe_detail,
                            }
                    if image_result_is_unknown(safe_result.detail or {}):
                        return {
                            "group_name": self.group_name,
                            "status": "failed",
                            "success": False,
                            "detail": safe_result.error,
                            "error_type": IMAGE_GENERATION_FAILED,
                            "generator_detail": safe_result.detail or {},
                        }
                    safe_failure_detail = str(safe_result.error or "安全化 Prompt 生图失败")[:160]
                except Exception as safe_exc:
                    safe_failure_detail = f"安全化 Prompt 阶段异常：{str(safe_exc)[:140]}"
            try:
                return self._local_fallback(policy_code or IMAGE_GENERATION_FAILED)
            except Exception as fallback_exc:
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": (
                        f"{result.error}；{safe_failure_detail}；"
                        f"本地兜底失败：{str(fallback_exc)[:120]}"
                    ),
                    "error_type": IMAGE_GENERATION_FAILED,
                    "generator_detail": generator_detail,
                }
        ok, detail = verify_image_contract(self.prompt_file, self.output_path)
        if not ok:
            from app.image.fact_verification import strict_fact_verification_enabled

            if strict_fact_verification_enabled(self.prompt_file):
                try:
                    if self.output_path.exists():
                        self.output_path.unlink()
                    retry_result = self._call_generator(
                        self.prompt_file,
                        quality_retry=True,
                    )
                except Exception as exc:
                    try:
                        return self._local_fallback(IMAGE_CONTENT_VERIFICATION_FAILED)
                    except Exception as fallback_exc:
                        return {
                            "group_name": self.group_name,
                            "status": "failed",
                            "success": False,
                            "detail": (
                                f"第一次{detail}；第二次生图异常：{str(exc)[:140]}；"
                                f"本地兜底失败：{str(fallback_exc)[:100]}"
                            ),
                            "error_type": IMAGE_CONTENT_VERIFICATION_FAILED,
                        }
                if retry_result.success:
                    retry_ok, retry_detail = verify_image_contract(
                        self.prompt_file,
                        self.output_path,
                    )
                    if retry_ok:
                        retry_meta = dict(retry_result.detail or {})
                        retry_meta["fact_verification_retry"] = 2
                        return {
                            "group_name": self.group_name,
                            "status": "success",
                            "success": True,
                            "detail": f"第二次生图通过事实校验：{retry_detail}",
                            "error_type": "",
                            "generator_detail": retry_meta,
                        }
                    retry_error = retry_detail
                else:
                    retry_error = retry_result.error or "第二次生图失败"
                    if image_result_is_unknown(retry_result.detail or {}):
                        if self.output_path.exists():
                            self.output_path.unlink()
                        return {
                            "group_name": self.group_name,
                            "status": "failed",
                            "success": False,
                            "detail": f"第一次{detail}；第二次{retry_error}",
                            "error_type": IMAGE_GENERATION_FAILED,
                            "generator_detail": retry_result.detail or {},
                        }
                if self.output_path.exists():
                    self.output_path.unlink()
                try:
                    return self._local_fallback(IMAGE_CONTENT_VERIFICATION_FAILED)
                except Exception as fallback_exc:
                    return {
                        "group_name": self.group_name,
                        "status": "failed",
                        "success": False,
                        "detail": (
                            f"第一次{detail}；第二次{retry_error}；"
                            f"本地兜底失败：{str(fallback_exc)[:100]}"
                        ),
                        "error_type": IMAGE_CONTENT_VERIFICATION_FAILED,
                        "generator_detail": retry_result.detail or {},
                    }
            try:
                return self._local_fallback(IMAGE_FILE_MISSING)
            except Exception as fallback_exc:
                return {
                    "group_name": self.group_name,
                    "status": "failed",
                    "success": False,
                    "detail": f"生成后验证失败：{detail}；本地兜底失败：{str(fallback_exc)[:100]}",
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

    def _apply_hook(self, job: ImageJob, result: dict) -> dict:
        if not self.run_hook:
            return result
        try:
            self.run_hook(job, result)
        except Exception as exc:
            result.update(
                status="failed",
                success=False,
                error_type="IMAGE_STATE_PERSIST_FAILED",
                detail=f"图片结果状态持久化失败：{type(exc).__name__}: {str(exc)[:220]}",
                hook_error=True,
            )
        return result

    def run_all(self, jobs: list[ImageJob]) -> list[dict]:
        """受控并发执行，结果顺序始终与输入任务一致。"""
        if not jobs:
            return []
        if self.max_workers == 1 or len(jobs) == 1:
            results: list[dict] = []
            for job in jobs:
                result = self._run_timed(job)
                results.append(self._apply_hook(job, result))
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
                results_by_index[index] = self._apply_hook(job, result)
        return [results_by_index[index] for index in range(len(jobs))]
