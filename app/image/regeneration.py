"""运行级图片重新生成：持久化状态、全局串行、失败保留旧图。"""

from __future__ import annotations

import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import verify_image
from app.v2.constants import READY_TO_SEND, SENT
from app.v2.run_store import RunStore

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="groupbrief-image-regen")
_ACTIVE_LOCK = threading.Lock()
_ACTIVE: set[str] = set()
_POLICY_RE = re.compile(r"policy|safety|moderation|违规|安全策略|不允许", re.IGNORECASE)


def _job_key(group_name: str, run_date: str) -> str:
    return f"{group_name}\0{run_date}"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def enqueue_regeneration(
    settings: Settings,
    group_name: str,
    run_date: str,
    *,
    generator: Any | None = None,
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

    store.update(
        group_name,
        run_date,
        image_regen_status="queued",
        image_regen_error="",
        image_regen_requested_at=_now(),
        desktop_regen_requested=False,
        send_hold=True,
    )
    _EXECUTOR.submit(
        _run_regeneration,
        settings,
        group_name,
        run_date,
        generator or CodexImageGenerator(settings=settings),
    )
    return store.load_run(group_name, run_date)


def _fallback_allowed(error: str, detail: dict[str, Any] | None) -> bool:
    if _POLICY_RE.search(error or ""):
        return False
    stage = str((detail or {}).get("stage") or "")
    return stage in {"health", "exec", "save", "copy"}


def _run_regeneration(
    settings: Settings,
    group_name: str,
    run_date: str,
    generator: Any,
) -> None:
    store = RunStore(settings.output_dir)
    key = _job_key(group_name, run_date)
    temp_path = store.regenerating_image_path(group_name, run_date)
    try:
        if temp_path.exists():
            temp_path.unlink()
        store.update(group_name, run_date, image_regen_status="running", image_regen_started_at=_now())
        result = generator.generate(store.prompt_path(group_name, run_date), temp_path)
        if not result.success:
            fallback = _fallback_allowed(result.error, result.detail)
            store.update(
                group_name,
                run_date,
                image_regen_status="fallback_queued" if fallback else "failed",
                image_regen_error=result.error[:500],
                image_regen_finished_at=_now(),
                desktop_regen_requested=fallback,
                send_hold=True,
            )
            return

        ok, detail = verify_image(temp_path)
        if not ok:
            store.update(
                group_name,
                run_date,
                image_regen_status="fallback_queued",
                image_regen_error=detail[:500],
                image_regen_finished_at=_now(),
                desktop_regen_requested=True,
                send_hold=True,
            )
            return

        target = store.image_path(group_name, run_date)
        previous = store.previous_image_path(group_name, run_date)
        if target.is_file() and target.stat().st_size > 0:
            shutil.copy2(target, previous)
        temp_path.replace(target)
        current = store.load_run(group_name, run_date)
        next_status = SENT if current.get("status") == SENT else READY_TO_SEND
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
            text_sent_at="",
            image_status="regenerated",
            image_error=None,
        )
    except Exception as exc:
        store.update(
            group_name,
            run_date,
            image_regen_status="failed",
            image_regen_error=str(exc)[:500],
            image_regen_finished_at=_now(),
            send_hold=True,
        )
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        with _ACTIVE_LOCK:
            _ACTIVE.discard(key)


def run_regeneration_now(
    settings: Settings,
    group_name: str,
    run_date: str,
    generator: Any,
) -> dict[str, Any]:
    """同步测试入口，不进入线程池。"""
    key = _job_key(group_name, run_date)
    with _ACTIVE_LOCK:
        _ACTIVE.add(key)
    _run_regeneration(settings, group_name, run_date, generator)
    return RunStore(settings.output_dir).load_run(group_name, run_date)
