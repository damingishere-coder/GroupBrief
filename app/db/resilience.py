"""SQLite 短暂 busy/locked 的有限退避。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

T = TypeVar("T")


def is_sqlite_busy(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database is busy" in text or "sqlite_busy" in text


def run_with_sqlite_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.1,
) -> T:
    attempts = min(max(int(max_attempts), 1), 5)
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except OperationalError as exc:
            if not is_sqlite_busy(exc) or attempt >= attempts:
                raise
            time.sleep(max(float(base_delay_seconds), 0.01) * (2 ** (attempt - 1)))
    raise AssertionError("unreachable")
