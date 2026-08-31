from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from app.scheduler.daily_v2_job import DailyScheduleState
from app.v2.constants import PENDING
from app.v2.run_store import RunStore, _atomic_write_text


def test_atomic_write_retries_transient_windows_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    original_replace = os.replace
    attempts = 0

    def flaky_replace(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "access denied", str(source), str(destination))
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky_replace)
    monkeypatch.setattr("app.v2.run_store.time.sleep", lambda _seconds: None)

    _atomic_write_text(target, '{"ok": true}')

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_does_not_hide_persistent_replace_failure(tmp_path, monkeypatch):
    target = tmp_path / "state.json"

    def blocked_replace(source, destination):
        raise PermissionError(5, "access denied", str(source), str(destination))

    monkeypatch.setattr(os, "replace", blocked_replace)
    monkeypatch.setattr("app.v2.run_store.time.sleep", lambda _seconds: None)

    with pytest.raises(PermissionError):
        _atomic_write_text(target, '{"ok": false}')

    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def _run_store_writer(root: str, worker: int, count: int, queue) -> None:
    try:
        store = RunStore(Path(root))
        for index in range(count):
            store.update(
                "并发群",
                "2026-08-27",
                **{f"worker_{worker}_{index}": index},
            )
        queue.put("")
    except Exception as exc:  # pragma: no cover - 返回给父进程精确失败证据
        queue.put(f"{type(exc).__name__}: {exc}")


def _schedule_state_writer(root: str, worker: int, count: int, queue) -> None:
    try:
        state = DailyScheduleState(Path(root))
        for index in range(count):
            state.update(
                "2026-08-27",
                generation_started_at="2026-08-27T00:15:00+08:00",
                generation_status="running",
                **{f"worker_{worker}_{index}": index},
            )
        queue.put("")
    except Exception as exc:  # pragma: no cover
        queue.put(f"{type(exc).__name__}: {exc}")


def _run_two_processes(target, root: Path, count: int = 12) -> None:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(target=target, args=(str(root), worker, count, queue))
        for worker in (1, 2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert [queue.get(timeout=2) for _ in processes] == ["", ""]


def test_run_store_cross_process_updates_do_not_lose_fields(tmp_path):
    root = tmp_path / "output"
    store = RunStore(root)
    store.save_run("并发群", "2026-08-27", {"status": PENDING})

    _run_two_processes(_run_store_writer, root)

    run = store.load_run("并发群", "2026-08-27")
    for worker in (1, 2):
        for index in range(12):
            assert run[f"worker_{worker}_{index}"] == index
    assert run["state_version"] >= 25
    assert not list(root.rglob("*.tmp"))


def test_scheduler_state_cross_process_updates_do_not_lose_fields(tmp_path):
    _run_two_processes(_schedule_state_writer, tmp_path)

    state = DailyScheduleState(tmp_path).load("2026-08-27")
    for worker in (1, 2):
        for index in range(12):
            assert state[f"worker_{worker}_{index}"] == index
    assert state["state_version"] >= 24
    assert not list(tmp_path.rglob("*.tmp"))
