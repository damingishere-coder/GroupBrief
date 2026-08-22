"""GroupBrief 生成阶段的有界并发原语。

群级 worker 由调用方按一次运行创建；微信取数和总结模型请求使用这里的
进程级共享信号量，避免多个 V1/V2 入口叠加后突破全局上限。
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Iterator, Sequence, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_REGISTRY_LOCK = threading.Lock()
_SEMAPHORES: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_EXECUTORS: dict[int, ThreadPoolExecutor] = {}


def normalized_limit(value: object, default: int, maximum: int = 32) -> int:
    """把可持久化配置收敛为安全的正整数并发上限。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _semaphore(name: str, limit: int) -> threading.BoundedSemaphore:
    key = (name, limit)
    with _REGISTRY_LOCK:
        return _SEMAPHORES.setdefault(key, threading.BoundedSemaphore(limit))


@contextmanager
def bounded_slot(name: str, limit: int) -> Iterator[None]:
    """领取一个命名的全局并发槽并在退出时可靠释放。"""
    gate = _semaphore(name, normalized_limit(limit, 1))
    gate.acquire()
    try:
        yield
    finally:
        gate.release()


def _executor(limit: int) -> ThreadPoolExecutor:
    limit = normalized_limit(limit, 1)
    with _REGISTRY_LOCK:
        executor = _EXECUTORS.get(limit)
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=limit,
                thread_name_prefix=f"groupbrief-ai-{limit}",
            )
            _EXECUTORS[limit] = executor
        return executor


def run_ai_tasks_ordered(
    fn: Callable[[T], R],
    items: Sequence[T],
    *,
    max_workers: int,
) -> list[R]:
    """在共享 AI 线程池并行执行，并按输入顺序返回或抛出首个异常。"""
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0])]
    executor = _executor(max_workers)
    futures: list[Future[R]] = [executor.submit(fn, item) for item in items]
    return [future.result() for future in futures]
