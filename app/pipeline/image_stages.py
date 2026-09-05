"""DailyPipeline 的受控并发图片任务阶段实现。"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Callable
import uuid

from app.image.delivery_guard import image_delivery_eligible, image_provenance_complete
from app.image.image_task import ImageJob, SerialImageQueue
from app.image.regeneration import normalize_candidate_diagnostics
from app.core.logging import get_logger
from app.core.observability import log_event
from app.v2.constants import (
    FAILED,
    IMAGE_FALLBACK_NOT_SENDABLE,
    IMAGE_PROVENANCE_MISSING,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    READY_TO_SEND,
)
from app.v2.run_store import RunStore

logger = get_logger("groupbrief.pipeline")


class ImageStages:
    """构造、记录并收口受控并发的图片任务。"""

    def __init__(
        self,
        *,
        store: RunStore,
        image_generator,
        consume_image_theme: Callable[[int, str, str, str], dict] | None = None,
    ) -> None:
        self.store = store
        self.image_generator = image_generator
        self.consume_image_theme = consume_image_theme

    def make_job(self, group_name: str, run_date: str, force: bool) -> ImageJob:
        prompt_path = self.store.prompt_path(group_name, run_date)
        current = self.store.load_run(group_name, run_date)
        prompt_meta = (
            current.get("prompt_meta")
            if isinstance(current.get("prompt_meta"), dict)
            else {}
        )
        snapshot_hash = str(current.get("message_snapshot_sha256") or "")
        speaker_fingerprint = str(current.get("speaker_fingerprint") or "")
        if (
            current.get("prompt_stale") is not False
            or not snapshot_hash
            or not speaker_fingerprint
            or str(prompt_meta.get("message_snapshot_sha256") or "") != snapshot_hash
            or str(prompt_meta.get("speaker_fingerprint") or "")
            != speaker_fingerprint
        ):
            raise ValueError("Prompt 与消息快照归属契约不一致，已停止生图")
        prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        previous = current.get("image_job") if isinstance(current.get("image_job"), dict) else {}
        if (
            not force
            and previous.get("job_id")
            and previous.get("prompt_sha256") == prompt_sha256
        ):
            job_id = str(previous["job_id"])
            revision = max(1, int(previous.get("revision") or 1))
        else:
            job_id = uuid.uuid4().hex
            revision = max(1, int(previous.get("revision") or 0) + 1)
        image_job = {
            "job_id": job_id,
            "revision": revision,
            "prompt_sha256": prompt_sha256,
            "status": "queued",
            "queued_at": datetime.now().astimezone().isoformat(),
            "candidates": [],
        }
        self.store.update(group_name, run_date, image_job=image_job)
        return ImageJob(
            group_name=group_name,
            prompt_file=prompt_path,
            output_path=self.store.image_path(group_name, run_date),
            generator=self.image_generator,
            force=force,
            job_id=job_id,
            revision=revision,
            prompt_sha256=prompt_sha256,
        )

    def record_result(self, job: ImageJob, result: dict) -> None:
        run_date = job.output_path.parent.name
        current = self.store.load_run(job.group_name, run_date)
        theme_consumption: dict = {}
        applied_theme = str(current.get("image_theme") or "")
        if (
            result["success"]
            and self.consume_image_theme is not None
            and applied_theme not in {"", "ai_free", "random_preset"}
        ):
            try:
                group_id = int(current.get("group_id") or 0)
                if group_id <= 0:
                    raise ValueError("运行记录缺少有效 group_id")
                theme_consumption = self.consume_image_theme(
                    group_id,
                    run_date,
                    applied_theme,
                    str(current.get("image_theme_custom") or ""),
                )
            except Exception as exc:
                result = {
                    **result,
                    "success": False,
                    "status": "failed",
                    "error_type": "IMAGE_THEME_CONSUME_FAILED",
                    "detail": f"图片已生成但一次性主题消费失败，已停止发送：{str(exc)[:160]}",
                }
        status = IMAGE_READY if result["success"] else FAILED
        error_type = result.get("error_type") or IMAGE_GENERATION_FAILED
        error_detail = (
            str(result.get("detail") or "图片生成失败")[:300]
            if not result["success"]
            else None
        )
        stage_timings = dict(current.get("stage_timings") or {})
        imagegen_ms = int(result.get("imagegen_ms") or 0)
        stage_timings["imagegen_ms"] = imagegen_ms
        generator_detail = result.get("generator_detail")
        if not isinstance(generator_detail, dict):
            generator_detail = {}
        image_metadata = {
            "image_fallback_level": generator_detail.get("fallback_level"),
            "image_variant": generator_detail.get("image_variant"),
            "image_status": result.get("status"),
        }
        diagnostic_fallback = bool(
            int(generator_detail.get("fallback_level") or 0) >= 3
            or str(generator_detail.get("image_variant") or "").lower() == "pillow"
        )
        image_size_bytes = (
            job.output_path.stat().st_size
            if (result["success"] or diagnostic_fallback) and job.output_path.is_file()
            else 0
        )
        finished_at = datetime.now().astimezone().isoformat()
        theme_usage_recorded = bool(
            theme_consumption.get("consumed")
            or theme_consumption.get("already_consumed")
        )
        theme_just_consumed = bool(theme_consumption.get("consumed"))
        image_job = current.get("image_job") if isinstance(current.get("image_job"), dict) else {}
        candidates = normalize_candidate_diagnostics(generator_detail)
        if result["success"]:
            image_job_status = "completed"
        elif diagnostic_fallback:
            image_job_status = "diagnostic_fallback"
        elif generator_detail.get("outcome_unknown"):
            image_job_status = "result_unknown"
        elif generator_detail.get("stage") == "ambiguous" or candidates:
            image_job_status = "ambiguous_result"
        else:
            image_job_status = "failed"
        next_job = {
            **image_job,
            "status": image_job_status,
            "finished_at": finished_at,
            "receipt": {
                "job_id": job.job_id,
                "revision": job.revision,
                "prompt_sha256": job.prompt_sha256,
                "image_path": str(job.output_path.resolve()) if result["success"] else "",
                "diagnostic_path": (
                    str(job.output_path.resolve()) if diagnostic_fallback else ""
                ),
                "sha256": str(generator_detail.get("sha256") or ""),
                "source": str(generator_detail.get("receipt_source") or ""),
            },
            "candidates": candidates,
            "codex_thread_id": str(generator_detail.get("codex_thread_id") or ""),
        }
        self.store.update(
            job.group_name,
            run_date,
            status=status,
            failed_stage="image" if not result["success"] else None,
            error=error_detail,
            image_error=error_detail,
            image_status="success" if result["success"] else result["status"],
            error_type=error_type if not result["success"] else None,
            stage_timings=stage_timings,
            imagegen_ms=imagegen_ms,
            image_generated_at=(
                finished_at
                if result["success"]
                else current.get("image_generated_at")
            ),
            image_diagnostic_generated_at=(
                finished_at
                if diagnostic_fallback
                else ""
                if result["success"]
                else current.get("image_diagnostic_generated_at", "")
            ),
            image_size_bytes=image_size_bytes,
            image_attempt_count=int(generator_detail.get("attempt_count") or 0),
            image_recovery_status=str(generator_detail.get("recovery_status") or ""),
            image_recovered_at=str(generator_detail.get("recovered_at") or ""),
            image_receipt_source=str(generator_detail.get("receipt_source") or ""),
            recovery_status=str(generator_detail.get("recovery_status") or ""),
            recovered_at=str(generator_detail.get("recovered_at") or ""),
            receipt_source=str(generator_detail.get("receipt_source") or ""),
            codex_thread_id=str(generator_detail.get("codex_thread_id") or ""),
            codex_event_summary=generator_detail.get("codex_event_summary") or [],
            codex_stderr_tail=str(generator_detail.get("codex_stderr_tail") or ""),
            image_candidate_diagnostics=generator_detail.get("candidate_diagnostics") or [],
            image_attempts=generator_detail.get("attempts") or [],
            image_fallback_level=int(generator_detail.get("fallback_level") or 0),
            image_variant=str(generator_detail.get("image_variant") or "normal"),
            image_fallback_reason=str(generator_detail.get("fallback_reason") or ""),
            image_fallback_font=str(generator_detail.get("fallback_font") or ""),
            image_safety_redactions=generator_detail.get("safety_redactions") or [],
            image_theme_consumed=(
                True if theme_usage_recorded else current.get("image_theme_consumed", False)
            ),
            image_theme_consumed_at=(
                finished_at
                if theme_just_consumed
                else current.get("image_theme_consumed_at", "")
            ),
            image_theme_remaining_runs=(
                int(theme_consumption.get("remaining_runs") or 0)
                if theme_consumption
                else current.get("image_theme_remaining_runs", 0)
            ),
            image_theme_next=(
                str(theme_consumption.get("next_theme") or "random_preset")
                if theme_consumption
                else current.get("image_theme_next", "")
            ),
            image_force_local_fallback=(
                False if result["success"] else current.get("image_force_local_fallback", False)
            ),
            image_stale=False if result["success"] else current.get("image_stale", True),
            artifact_stale_reason=(
                "" if result["success"] else current.get("artifact_stale_reason", "")
            ),
            image_job=next_job,
        )
        latest = self.store.load_run(job.group_name, run_date)
        log_event(
            logger,
            "IMAGE_GENERATION_FINISHED",
            group_task_id=latest.get("group_task_id"),
            group_name=job.group_name,
            run_date=run_date,
            stage="IMAGE",
            status="success" if result["success"] else "failed",
            duration_ms=imagegen_ms,
            attempt=latest.get("image_attempt_count", 0),
            error_type=error_type if not result["success"] else "",
            error_summary=error_detail or "",
        )

    def advance_ready(self, job: ImageJob, run_date: str) -> None:
        run = self.store.load_run(job.group_name, run_date)
        if run.get("status") == IMAGE_READY:
            if not image_delivery_eligible(run):
                provenance_missing = not image_provenance_complete(run)
                error_type = (
                    IMAGE_PROVENANCE_MISSING
                    if provenance_missing
                    else IMAGE_FALLBACK_NOT_SENDABLE
                )
                detail = (
                    "图片来源元数据不完整，不可进入发送流程"
                    if provenance_missing
                    else "Level 3/Pillow 诊断图不可进入发送流程"
                )
                self.store.update(
                    job.group_name,
                    run_date,
                    status=FAILED,
                    failed_stage="image",
                    error=detail,
                    image_error=detail,
                    error_type=error_type,
                )
                return
            self.store.update(job.group_name, run_date, status=READY_TO_SEND)

    def run_jobs(
        self,
        image_jobs: list[ImageJob],
        run_date: str,
        *,
        run_hook: Callable[[ImageJob, dict], None],
        after_hook: Callable[[ImageJob, str], None],
    ) -> list[dict]:
        settings = getattr(self.image_generator, "settings", None)
        max_workers = int(getattr(settings, "image_generation_concurrency", 1) or 1)
        queue = SerialImageQueue(run_hook=run_hook, max_workers=max_workers)
        queue_results = queue.run_all(image_jobs)
        final_results: list[dict] = []
        for job, queue_result in zip(image_jobs, queue_results):
            if queue_result.get("hook_error"):
                final_results.append(
                    {
                        "group_name": job.group_name,
                        "status": "failed",
                        "error_type": "IMAGE_STATE_PERSIST_FAILED",
                        "detail": queue_result.get("detail")
                        or "图片结果状态持久化失败",
                        "failed_stage": "image",
                    }
                )
                continue
            after_hook(job, run_date)
            run = self.store.load_run(job.group_name, run_date)
            final_status = run.get("status")
            if final_status == READY_TO_SEND:
                final_results.append(
                    {
                        "group_name": job.group_name,
                        "status": "ready_to_send",
                        "detail": "图片已准备，可以发送",
                        "receipt_source": str(run.get("image_receipt_source") or ""),
                        "recovery_status": str(run.get("image_recovery_status") or ""),
                        "recovered_at": str(run.get("image_recovered_at") or ""),
                        "codex_thread_id": str(run.get("codex_thread_id") or ""),
                    }
                )
                continue
            if final_status == FAILED:
                final_results.append(
                    {
                        "group_name": job.group_name,
                        "status": "failed",
                        "error_type": (
                            run.get("error_type")
                            or queue_result.get("error_type")
                            or IMAGE_GENERATION_FAILED
                        ),
                        "detail": (
                            run.get("image_error")
                            or run.get("error")
                            or queue_result.get("detail")
                            or "生图失败"
                        ),
                        "failed_stage": str(run.get("failed_stage") or "image"),
                        "recovery_status": str(run.get("image_recovery_status") or ""),
                        "codex_thread_id": str(run.get("codex_thread_id") or ""),
                    }
                )
                continue
            final_results.append(
                {
                    "group_name": job.group_name,
                    "status": str(
                        final_status or queue_result.get("status") or "failed"
                    ).lower(),
                    "detail": queue_result.get("detail") or "图片任务未进入终态",
                }
            )
        return final_results
