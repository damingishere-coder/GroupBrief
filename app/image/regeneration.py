"""运行级图片重画：稳定任务身份、受控并发、失败关闭与人工候选认领。"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai.concurrency import bounded_slot, normalized_limit
from app.config.settings import Settings
from app.core.logging import get_logger
from app.image.codex_generator import CodexImageGenerator
from app.image.fact_verification import strict_fact_verification_enabled
from app.image.image_task import ImageTaskResult, verify_image_contract
from app.v2.constants import (
    IMAGE_CONTENT_VERIFICATION_FAILED,
    IMAGE_FILE_MISSING,
    IMAGE_GENERATION_FAILED,
    READY_TO_SEND,
    SENT,
)
from app.v2.run_store import RunStore

_EXECUTOR = ThreadPoolExecutor(max_workers=6, thread_name_prefix="groupbrief-image-regen")
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: set[str] = set()
logger = get_logger("groupbrief.image.regeneration")


def _job_key(group_name: str, run_date: str) -> str:
    return f"{group_name}\0{run_date}"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _prompt_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _job_revision(run: dict[str, Any]) -> int:
    previous = run.get("image_regen_job")
    if not isinstance(previous, dict):
        return 1
    try:
        return max(1, int(previous.get("revision") or 0) + 1)
    except (TypeError, ValueError):
        return 1


def _prepare_job(
    store: RunStore,
    group_name: str,
    run_date: str,
    *,
    expected_group_id: int | None = None,
    expected_wechat_group_id: str = "",
) -> dict[str, Any]:
    run = store.load_run(group_name, run_date)
    if expected_group_id is not None:
        try:
            actual_group_id = int(run.get("group_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("run.json 缺少稳定 group_id") from exc
        if actual_group_id != int(expected_group_id):
            raise ValueError("目标 group_id 与 run.json 不匹配")
    if expected_wechat_group_id:
        actual_wechat_group_id = str(run.get("wechat_group_id") or "").strip()
        if actual_wechat_group_id != expected_wechat_group_id.strip():
            raise ValueError("目标 wechat_group_id 与 run.json 不匹配")

    prompt_path = store.prompt_path(group_name, run_date)
    job_id = uuid.uuid4().hex
    revision = _job_revision(run)
    job = {
        "job_id": job_id,
        "revision": revision,
        "prompt_sha256": _prompt_sha256(prompt_path),
        "group_id": run.get("group_id"),
        "wechat_group_id": run.get("wechat_group_id") or "",
        "group_name": group_name,
        "run_date": run_date,
        "status": "queued",
        "requested_at": _now(),
        "attempt_dir": f".imagegen-jobs/{job_id}",
        "receipt": {},
        "candidates": [],
    }
    store.update(
        group_name,
        run_date,
        image_regen_job=job,
        image_regen_status="queued",
        image_regen_error="",
        image_regen_requested_at=job["requested_at"],
        desktop_regen_requested=False,
        send_hold=True,
        needs_manual_send=True,
    )
    return job


def _merge_job(
    store: RunStore,
    group_name: str,
    run_date: str,
    job_id: str,
    **fields: Any,
) -> dict[str, Any]:
    run = store.load_run(group_name, run_date)
    current = run.get("image_regen_job")
    if not isinstance(current, dict) or str(current.get("job_id") or "") != job_id:
        raise RuntimeError("生图任务身份已变化，拒绝写入旧任务结果")
    merged = {**current, **fields}
    store.update(group_name, run_date, image_regen_job=merged)
    return merged


def _invoke_generator(
    generator: Any,
    prompt_path: Path,
    output_path: Path,
    job: dict[str, Any],
) -> ImageTaskResult:
    parameters = inspect.signature(generator.generate).parameters
    if "job_id" in parameters:
        return generator.generate(
            prompt_path,
            output_path,
            force=True,
            job_id=str(job["job_id"]),
            revision=int(job["revision"]),
            prompt_sha256=str(job["prompt_sha256"]),
        )
    if "force" in parameters:
        return generator.generate(prompt_path, output_path, force=True)
    return generator.generate(prompt_path, output_path)


def _enforce_job_identity(
    generator: Any,
    result: ImageTaskResult,
    job: dict[str, Any],
) -> ImageTaskResult:
    """支持稳定任务回执的生成器必须返回与本次尝试一致的身份。"""
    detail = result.detail if isinstance(result.detail, dict) else {}
    supports_identity = "job_id" in inspect.signature(generator.generate).parameters
    if not result.success or not supports_identity:
        return result
    if (
        str(detail.get("job_id") or "") == str(job["job_id"])
        and str(detail.get("prompt_sha256") or "").lower()
        == str(job["prompt_sha256"]).lower()
    ):
        return result
    return ImageTaskResult(
        False,
        error="生图回执的 job_id 或 Prompt 哈希不匹配，拒绝自动认领",
        detail={
            **detail,
            "stage": "ambiguous",
            "outcome_unknown": True,
            "candidate_diagnostics": detail.get("candidate_diagnostics") or [],
        },
    )


def normalize_candidate_diagnostics(detail: dict[str, Any]) -> list[dict[str, Any]]:
    raw = detail.get("candidate_diagnostics")
    if not isinstance(raw, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sha = str(item.get("sha256") or "").upper()
        root = str(item.get("root") or "")
        relative = str(item.get("relative_path") or "")
        if not re.fullmatch(r"[A-F0-9]{64}", sha) or root not in {"task", "generated_images"} or not relative:
            continue
        candidates.append(
            {
                "candidate_id": sha.lower(),
                "sha256": sha,
                "root": root,
                "relative_path": relative,
                "size_bytes": int(item.get("size_bytes") or 0),
                "sources": list(item.get("sources") or []),
            }
        )
    return candidates


def _failure_status(detail: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    if candidates or str(detail.get("stage") or "") == "ambiguous":
        return "ambiguous_result"
    if bool(detail.get("outcome_unknown")):
        return "result_unknown"
    return "failed"


def _promote_image(
    store: RunStore,
    group_name: str,
    run_date: str,
    source: Path,
    *,
    recovery_status: str,
    generator_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generator_meta = generator_detail if isinstance(generator_detail, dict) else {}
    prompt_path = store.prompt_path(group_name, run_date)
    ok, contract_detail = verify_image_contract(prompt_path, source)
    if not ok:
        raise ValueError(contract_detail)
    target = store.image_path(group_name, run_date)
    previous = store.previous_image_path(group_name, run_date)
    staging = store.regenerating_image_path(group_name, run_date)
    source = source.resolve()
    staging = staging.resolve()
    if source != staging:
        if staging.exists():
            staging.unlink()
        shutil.copy2(source, staging)
    if target.is_file() and target.stat().st_size > 0:
        shutil.copy2(target, previous)
    os.replace(staging, target)
    current = store.load_run(group_name, run_date)
    next_status = SENT if current.get("status") == SENT else READY_TO_SEND
    success_fields: dict[str, Any] = {}
    if str(current.get("error_type") or "") in {
        IMAGE_CONTENT_VERIFICATION_FAILED,
        IMAGE_GENERATION_FAILED,
        IMAGE_FILE_MISSING,
    }:
        success_fields.update(error=None, error_type=None, failed_stage=None)
    store.update(
        group_name,
        run_date,
        status=next_status,
        image_regen_status="ready_for_review",
        image_regen_error="",
        image_regen_finished_at=_now(),
        image_regenerated_at=_now(),
        desktop_regen_requested=False,
        send_hold=True,
        needs_manual_send=True,
        image_status="regenerated",
        image_error=None,
        image_recovery_status=recovery_status,
        image_recovered_at=str(generator_meta.get("recovered_at") or ""),
        image_receipt_source=str(generator_meta.get("receipt_source") or ""),
        image_attempt_count=int(generator_meta.get("attempt_count") or 0),
        image_attempts=generator_meta.get("attempts") or [],
        image_fallback_level=int(generator_meta.get("fallback_level") or 0),
        image_variant=str(generator_meta.get("image_variant") or "normal"),
        image_fallback_reason=str(generator_meta.get("fallback_reason") or ""),
        image_fallback_font=str(generator_meta.get("fallback_font") or ""),
        image_safety_redactions=generator_meta.get("safety_redactions") or [],
        image_force_local_fallback=False,
        codex_thread_id=str(generator_meta.get("codex_thread_id") or ""),
        codex_event_summary=generator_meta.get("codex_event_summary") or [],
        codex_stderr_tail=str(generator_meta.get("codex_stderr_tail") or ""),
        image_candidate_diagnostics=generator_meta.get("candidate_diagnostics") or [],
        image_size_bytes=target.stat().st_size,
        image_sha256=_sha256(target),
        **success_fields,
    )
    return store.load_run(group_name, run_date)


def enqueue_regeneration(
    settings: Settings,
    group_name: str,
    run_date: str,
    *,
    generator: Any | None = None,
    expected_group_id: int | None = None,
    expected_wechat_group_id: str = "",
) -> dict[str, Any]:
    store = RunStore(settings.output_dir)
    if not store.run_path(group_name, run_date).exists():
        raise FileNotFoundError("运行记录不存在")
    if not store.prompt_path(group_name, run_date).is_file():
        raise FileNotFoundError("image_prompt.txt 不存在")

    key = _job_key(group_name, run_date)
    with _ACTIVE_LOCK:
        run = store.load_run(group_name, run_date)
        if key in _ACTIVE or run.get("image_regen_status") in {"queued", "running"}:
            raise RuntimeError("该运行已在重新生图队列中")
        _ACTIVE.add(key)
    try:
        job = _prepare_job(
            store,
            group_name,
            run_date,
            expected_group_id=expected_group_id,
            expected_wechat_group_id=expected_wechat_group_id,
        )
        _EXECUTOR.submit(
            _run_regeneration,
            settings,
            group_name,
            run_date,
            generator or CodexImageGenerator(settings=settings),
            job,
        )
    except Exception:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(key)
        raise
    return store.load_run(group_name, run_date)


def _run_regeneration(
    settings: Settings,
    group_name: str,
    run_date: str,
    generator: Any,
    job: dict[str, Any],
) -> None:
    store = RunStore(settings.output_dir)
    key = _job_key(group_name, run_date)
    temp_path = store.regenerating_image_path(group_name, run_date)
    job_id = str(job["job_id"])
    try:
        if temp_path.exists():
            temp_path.unlink()
        started_at = _now()
        _merge_job(store, group_name, run_date, job_id, status="running", started_at=started_at)
        store.update(
            group_name,
            run_date,
            image_regen_status="running",
            image_regen_started_at=started_at,
            send_hold=True,
            needs_manual_send=True,
        )
        limit = normalized_limit(getattr(settings, "image_generation_concurrency", 2), 2, maximum=6)
        prompt_path = store.prompt_path(group_name, run_date)
        with bounded_slot("image_regeneration", limit):
            result = _enforce_job_identity(
                generator,
                _invoke_generator(generator, prompt_path, temp_path, job),
                job,
            )
            if result.success and strict_fact_verification_enabled(prompt_path):
                first_ok, first_detail = verify_image_contract(prompt_path, temp_path)
                if not first_ok:
                    if temp_path.exists():
                        temp_path.unlink()
                    retry_job = {
                        **job,
                        "job_id": f"{job_id}-quality-2",
                        "revision": int(job["revision"]) + 1,
                    }
                    retry_result = _enforce_job_identity(
                        generator,
                        _invoke_generator(generator, prompt_path, temp_path, retry_job),
                        retry_job,
                    )
                    if retry_result.success:
                        retry_ok, retry_detail = verify_image_contract(
                            prompt_path,
                            temp_path,
                        )
                        if retry_ok:
                            retry_meta = dict(retry_result.detail or {})
                            retry_meta.update(
                                generation_attempt_job_id=str(
                                    retry_meta.get("job_id") or retry_job["job_id"]
                                ),
                                job_id=job_id,
                                revision=int(job["revision"]),
                                prompt_sha256=str(job["prompt_sha256"]),
                                fact_verification_retry=2,
                            )
                            result = ImageTaskResult(
                                True,
                                image_path=temp_path,
                                detail=retry_meta,
                            )
                        else:
                            result = ImageTaskResult(
                                False,
                                error=f"第一次{first_detail}；第二次{retry_detail}",
                                detail={
                                    **dict(retry_result.detail or {}),
                                    "stage": "fact_verify",
                                    "fact_verification_retry": 2,
                                },
                            )
                    else:
                        result = ImageTaskResult(
                            False,
                            error=(
                                f"第一次{first_detail}；第二次"
                                f"{retry_result.error or '生图失败'}"
                            ),
                            detail={
                                **dict(retry_result.detail or {}),
                                "fact_verification_retry": 2,
                            },
                        )
        detail = result.detail if isinstance(result.detail, dict) else {}
        candidates = normalize_candidate_diagnostics(detail)
        receipt = {
            "success": bool(result.success),
            "job_id": str(detail.get("job_id") or job_id),
            "revision": int(detail.get("revision") or job["revision"]),
            "prompt_sha256": str(detail.get("prompt_sha256") or job["prompt_sha256"]),
            "recovery_status": str(detail.get("recovery_status") or ""),
            "width": detail.get("width"),
            "height": detail.get("height"),
            "sha256": detail.get("sha256"),
            "attempt_count": int(detail.get("attempt_count") or 0),
            "receipt_source": str(detail.get("receipt_source") or ""),
            "codex_thread_id": str(detail.get("codex_thread_id") or ""),
        }
        if not result.success:
            status = _failure_status(detail, candidates)
            _merge_job(
                store,
                group_name,
                run_date,
                job_id,
                status=status,
                finished_at=_now(),
                error=result.error[:500],
                receipt=receipt,
                candidates=candidates,
            )
            store.update(
                group_name,
                run_date,
                image_regen_status=status,
                image_regen_error=result.error[:500],
                image_regen_finished_at=_now(),
                desktop_regen_requested=False,
                send_hold=True,
                needs_manual_send=True,
            )
            return

        run = _promote_image(
            store,
            group_name,
            run_date,
            temp_path,
            recovery_status="job_receipt_matched",
            generator_detail=detail,
        )
        _merge_job(
            store,
            group_name,
            run_date,
            job_id,
            status="ready_for_review",
            finished_at=_now(),
            receipt={
                **receipt,
                "success": True,
                "sha256": run.get("image_sha256"),
            },
            candidates=[],
        )
    except Exception as exc:
        try:
            _merge_job(
                store,
                group_name,
                run_date,
                job_id,
                status="failed",
                finished_at=_now(),
                error=str(exc)[:500],
            )
        except Exception:
            logger.exception(
                "重生图失败状态无法写入 job ledger：group=%s date=%s job=%s",
                group_name,
                run_date,
                job_id,
            )
        store.update(
            group_name,
            run_date,
            image_regen_status="failed",
            image_regen_error=str(exc)[:500],
            image_regen_finished_at=_now(),
            send_hold=True,
            needs_manual_send=True,
        )
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            logger.warning("重生图临时文件清理失败：%s", temp_path, exc_info=True)
        with _ACTIVE_LOCK:
            _ACTIVE.discard(key)


def _candidate_roots(settings: Settings, store: RunStore, group_name: str, run_date: str) -> dict[str, Path]:
    generator = CodexImageGenerator(settings=settings)
    return {
        "task": store.group_dir(group_name, run_date).resolve(),
        "generated_images": generator.generated_images_dir.resolve(),
    }


def _resolve_candidate(
    settings: Settings,
    group_name: str,
    run_date: str,
    candidate_id: str,
) -> tuple[Path, dict[str, Any], dict[str, Any], str]:
    store = RunStore(settings.output_dir)
    run = store.load_run(group_name, run_date)
    job_key = "image_regen_job"
    job = run.get(job_key)
    if not isinstance(job, dict) or not isinstance(job.get("candidates"), list) or not job.get("candidates"):
        job_key = "image_job"
        job = run.get(job_key)
    if not isinstance(job, dict):
        raise FileNotFoundError("该运行没有可恢复的生图任务")
    candidates = job.get("candidates")
    if not isinstance(candidates, list):
        raise FileNotFoundError("该运行没有候选图片")
    record = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and str(item.get("candidate_id") or "") == candidate_id.lower()
        ),
        None,
    )
    if record is None:
        raise FileNotFoundError("候选图片不存在")
    roots = _candidate_roots(settings, store, group_name, run_date)
    root = roots.get(str(record.get("root") or ""))
    if root is None:
        raise ValueError("候选图片根目录无效")
    path = (root / str(record.get("relative_path") or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("候选图片路径越界") from exc
    if not path.is_file() or _sha256(path) != str(record.get("sha256") or "").upper():
        raise FileNotFoundError("候选图片已缺失或内容发生变化")
    return path, record, job, job_key


def list_regeneration_candidates(
    settings: Settings,
    group_name: str,
    run_date: str,
) -> list[dict[str, Any]]:
    store = RunStore(settings.output_dir)
    run = store.load_run(group_name, run_date)
    job = run.get("image_regen_job")
    if not isinstance(job, dict) or not job.get("candidates"):
        job = run.get("image_job")
    if not isinstance(job, dict):
        return []
    result: list[dict[str, Any]] = []
    for item in job.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "")
        try:
            path, record, _, _ = _resolve_candidate(
                settings,
                group_name,
                run_date,
                candidate_id,
            )
        except (FileNotFoundError, ValueError):
            continue
        result.append(
            {
                **record,
                "job_id": str(job.get("job_id") or ""),
                "group_id": run.get("group_id"),
                "wechat_group_id": run.get("wechat_group_id") or "",
                "group_name": group_name,
                "run_date": run_date,
                "size_bytes": path.stat().st_size,
            }
        )
    return result


def candidate_preview_path(
    settings: Settings,
    group_name: str,
    run_date: str,
    candidate_id: str,
) -> Path:
    path, _, _, _ = _resolve_candidate(settings, group_name, run_date, candidate_id)
    return path


def claim_regeneration_candidate(
    settings: Settings,
    group_name: str,
    run_date: str,
    *,
    job_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    store = RunStore(settings.output_dir)
    source, record, job, job_key = _resolve_candidate(
        settings,
        group_name,
        run_date,
        candidate_id,
    )
    if str(job.get("job_id") or "") != job_id:
        raise ValueError("job_id 与当前运行不匹配")
    run = _promote_image(
        store,
        group_name,
        run_date,
        source,
        recovery_status="manually_claimed_candidate",
    )
    fields = {
        **job,
        "status": "ready_for_review",
        "finished_at": _now(),
        "receipt": {
            "success": True,
            "job_id": job_id,
            "revision": job.get("revision"),
            "prompt_sha256": job.get("prompt_sha256"),
            "sha256": run.get("image_sha256"),
            "recovery_status": "manually_claimed_candidate",
        },
        "claimed_candidate": record,
        "candidates": [],
    }
    if job_key == "image_regen_job":
        _merge_job(
            store,
            group_name,
            run_date,
            job_id,
            status="ready_for_review",
            finished_at=fields["finished_at"],
            receipt=fields["receipt"],
            claimed_candidate=record,
            candidates=[],
        )
    else:
        current = store.load_run(group_name, run_date)
        current_job = current.get("image_job")
        if not isinstance(current_job, dict) or str(current_job.get("job_id") or "") != job_id:
            raise ValueError("job_id 与当前运行不匹配")
        store.update(group_name, run_date, image_job=fields)
    return store.load_run(group_name, run_date)


def recover_pending_regenerations(settings: Settings) -> int:
    """服务重启后复用同一 job_id；结果未知由生成器转人工，不新建付费任务。"""
    store = RunStore(settings.output_dir)
    recovered = 0
    for run in store.list_runs():
        if run.get("image_regen_status") not in {"queued", "running"}:
            continue
        job = run.get("image_regen_job")
        group_name = str(run.get("group_name") or "")
        run_date = str(run.get("run_date") or "")
        if not isinstance(job, dict) or not group_name or not run_date:
            continue
        key = _job_key(group_name, run_date)
        with _ACTIVE_LOCK:
            if key in _ACTIVE:
                continue
            _ACTIVE.add(key)
        try:
            _EXECUTOR.submit(
                _run_regeneration,
                settings,
                group_name,
                run_date,
                CodexImageGenerator(settings=settings),
                job,
            )
            recovered += 1
        except Exception:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(key)
    return recovered


def run_regeneration_now(
    settings: Settings,
    group_name: str,
    run_date: str,
    generator: Any,
) -> dict[str, Any]:
    """同步测试入口，不进入线程池。"""
    store = RunStore(settings.output_dir)
    key = _job_key(group_name, run_date)
    with _ACTIVE_LOCK:
        if key in _ACTIVE:
            raise RuntimeError("该运行已在重新生图队列中")
        _ACTIVE.add(key)
    try:
        job = _prepare_job(store, group_name, run_date)
    except Exception:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(key)
        raise
    _run_regeneration(settings, group_name, run_date, generator, job)
    return store.load_run(group_name, run_date)
