from __future__ import annotations

import multiprocessing
from pathlib import Path

from app.scheduler.daily_v2_job import DailyScheduleState
from app.v2.constants import PENDING
from app.v2.run_store import RunStore


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


def test_scheduler_state_cross_process_updates_do_not_lose_fields(tmp_path):
    _run_two_processes(_schedule_state_writer, tmp_path)

    state = DailyScheduleState(tmp_path).load("2026-08-27")
    for worker in (1, 2):
        for index in range(12):
            assert state[f"worker_{worker}_{index}"] == index
    assert state["state_version"] >= 24
