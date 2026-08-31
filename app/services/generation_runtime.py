"""V1/V2/定时任务共用的生成互斥锁。"""

from __future__ import annotations

import ctypes
import os
import re
import threading
from contextlib import contextmanager
from typing import Iterator


class GenerationBusyError(TimeoutError):
    """已有日报生成任务正在运行。"""


_THREAD_LOCK = threading.Lock()
_MUTEX_NAME = "Local\\GroupBrief.Generation"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80


def generation_mutex_name() -> str:
    """返回当前进程使用的生成锁名。

    生产环境不设置命名空间，继续使用历史锁名。测试和离线仿真必须设置
    ``GROUPBRIEF_GENERATION_MUTEX_NAMESPACE``，从而不会与正在运行的 8766
    实例争抢同一个 Windows 内核对象。
    """

    raw = os.environ.get("GROUPBRIEF_GENERATION_MUTEX_NAMESPACE", "").strip()
    if not raw:
        return _MUTEX_NAME
    namespace = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.")[:80]
    return f"{_MUTEX_NAME}.{namespace}" if namespace else _MUTEX_NAME


@contextmanager
def generation_mutex(timeout_seconds: float = 2.0) -> Iterator[None]:
    """同时覆盖当前进程线程与 Windows 跨进程实例。"""
    timeout_seconds = max(float(timeout_seconds), 0.1)
    if not _THREAD_LOCK.acquire(timeout=timeout_seconds):
        raise GenerationBusyError("已有群报生成任务运行中")

    handle = None
    owns_handle = False
    try:
        if os.name == "nt":
            handle = ctypes.windll.kernel32.CreateMutexW(
                None, False, generation_mutex_name()
            )
            if not handle:
                raise OSError("无法创建群报生成互斥锁")
            wait_code = ctypes.windll.kernel32.WaitForSingleObject(
                handle, int(timeout_seconds * 1000)
            )
            if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise GenerationBusyError("已有群报生成任务在其他进程运行中")
            owns_handle = True
        yield
    finally:
        if handle:
            if owns_handle:
                ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        _THREAD_LOCK.release()
