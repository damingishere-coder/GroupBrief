"""DailyPipeline 的图片任务阶段实现。"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from app.image.image_task import ImageJob, SerialImageQueue
from app.v2.constants import (
    FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    READY_TO_SEND,
)
from app.v2.run_store import RunStore


class ImageStages:
    """构造、记录并收口严格串行的图片任务。"""

    def __init__(self, *, store: RunStore, image_generator) -> None:
        self.store = store
        self.image_generator = image_generator

    def make_job(self, group_name: str, run_date: str, force: bool) -> ImageJob:
        return ImageJob(
            group_name=group_name,
            prompt_file=self.store.prompt_path(group_name, run_date),
            output_path=self.store.image_path(group_name, run_date),
            generator=self.image_generator,
            force=force,
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
            image_candidate_diagnostics=generator_detail.get("candidate_diagnostics") or [],
            image_attempts=generator_detail.get("attempts") or [],
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
        queue = SerialImageQueue(run_hook=run_hook)
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
