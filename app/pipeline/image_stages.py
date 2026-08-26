"""DailyPipeline 的受控并发图片任务阶段实现。"""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Callable
import uuid

from app.image.image_task import ImageJob, SerialImageQueue
from app.image.regeneration import normalize_candidate_diagnostics
from app.v2.constants import (
    FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    READY_TO_SEND,
)
from app.v2.run_store import RunStore


class ImageStages:
    """构造、记录并收口受控并发的图片任务。"""

    def __init__(self, *, store: RunStore, image_generator) -> None:
        self.store = store
        self.image_generator = image_generator

    def make_job(self, group_name: str, run_date: str, force: bool) -> ImageJob:
        prompt_path = self.store.prompt_path(group_name, run_date)
        prompt_sha256 = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        current = self.store.load_run(group_name, run_date)
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
        status = IMAGE_READY if result["success"] else FAILED
        error_type = result.get("error_type") or IMAGE_GENERATION_FAILED
        error_detail = (
            str(result.get("detail") or "图片生成失败")[:300]
            if not result["success"]
            else None
        )
        run_date = job.output_path.parent.name
        current = self.store.load_run(job.group_name, run_date)
        stage_timings = dict(current.get("stage_timings") or {})
        imagegen_ms = int(result.get("imagegen_ms") or 0)
        stage_timings["imagegen_ms"] = imagegen_ms
        image_size_bytes = (
            job.output_path.stat().st_size
            if result["success"] and job.output_path.is_file()
            else 0
        )
        generator_detail = result.get("generator_detail")
        if not isinstance(generator_detail, dict):
            generator_detail = {}
        image_job = current.get("image_job") if isinstance(current.get("image_job"), dict) else {}
        candidates = normalize_candidate_diagnostics(generator_detail)
        next_job = {
            **image_job,
            "status": "completed" if result["success"] else (
                "result_unknown"
                if generator_detail.get("outcome_unknown")
                else "ambiguous_result"
                if generator_detail.get("stage") == "ambiguous" or candidates
                else "failed"
            ),
            "finished_at": datetime.now().astimezone().isoformat(),
            "receipt": {
                "job_id": job.job_id,
                "revision": job.revision,
                "prompt_sha256": job.prompt_sha256,
                "image_path": str(job.output_path.resolve()) if result["success"] else "",
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
            image_status=result["status"],
            error_type=error_type if not result["success"] else None,
            stage_timings=stage_timings,
            imagegen_ms=imagegen_ms,
            image_generated_at=(
                datetime.now().astimezone().isoformat()
                if result["success"]
                else current.get("image_generated_at")
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
            image_job=next_job,
        )

    def advance_ready(self, job: ImageJob, run_date: str) -> None:
        run = self.store.load_run(job.group_name, run_date)
        if run.get("status") == IMAGE_READY:
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
