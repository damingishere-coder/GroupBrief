"""V2 数据源有限重试与进程内熔断，不拼接多个 Provider 的消息。"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Callable

from app.config.settings import Settings
from app.data_sources.base import (
    DataSourceHealth,
    DataSourceStatus,
    FetchResult,
    ResolvedGroup,
    WeChatDataSource,
)
from app.v2.constants import MESSAGE_FETCH_FAILED, WECHAT_DATA_UNAVAILABLE


class ProviderCircuitBreaker:
    def __init__(self, *, threshold: int, cooldown_seconds: float) -> None:
        self.threshold = max(int(threshold), 1)
        self.cooldown_seconds = max(float(cooldown_seconds), 1.0)
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_probe = False
        self._lock = threading.Lock()

    def allow(self, now: float) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if now - self._opened_at >= self.cooldown_seconds:
                # 半开只放行一个探测，避免多个群在冷却结束时同时冲击 Provider。
                if self._half_open_probe:
                    return False
                self._half_open_probe = True
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_probe = False

    def failure(self, now: float) -> None:
        with self._lock:
            if self._half_open_probe:
                self._failures = self.threshold
            else:
                self._failures += 1
            if self._failures >= self.threshold:
                self._opened_at = now
            self._half_open_probe = False


class ResilientWeChatDataSource(WeChatDataSource):
    """只包装 fetch_messages；群解析仍委托原数据源并保留原语义。"""

    def __init__(
        self,
        source: WeChatDataSource,
        settings: Settings,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.source = source
        self.settings = settings
        self.name = source.name
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._circuit = ProviderCircuitBreaker(
            threshold=settings.wechat_fetch_circuit_failure_threshold,
            cooldown_seconds=settings.wechat_fetch_circuit_cooldown_seconds,
        )

    def health_check(self) -> DataSourceHealth:
        return self.source.health_check()

    def list_groups(self) -> list[ResolvedGroup]:
        return self.source.list_groups()

    def resolve_group(self, group_name: str) -> list[ResolvedGroup]:
        return self.source.resolve_group(group_name)

    @staticmethod
    def _retryable(result: FetchResult) -> bool:
        return bool(
            result.status in {DataSourceStatus.UNAVAILABLE, DataSourceStatus.READ_FAILED}
            and result.error_type
            in {"", MESSAGE_FETCH_FAILED, WECHAT_DATA_UNAVAILABLE, "API_5XX", "API_TIMEOUT_PRE_SUBMIT"}
        )

    def fetch_messages(self, group_id, start_time, end_time) -> FetchResult:
        now = self._clock()
        if not self._circuit.allow(now):
            return FetchResult(
                [],
                DataSourceStatus.UNAVAILABLE,
                "微信数据源熔断中，等待冷却后自动探测",
                WECHAT_DATA_UNAVAILABLE,
                {"attempts": 0, "circuit_open": True, "provider_chain": [self.name]},
            )

        max_attempts = min(max(int(self.settings.wechat_fetch_max_attempts), 1), 5)
        base = max(float(self.settings.wechat_fetch_retry_backoff_seconds), 0.0)
        attempts: list[dict] = []
        last_result: FetchResult | None = None
        for attempt in range(1, max_attempts + 1):
            started = self._clock()
            try:
                result = self.source.fetch_messages(group_id, start_time, end_time)
            except (TimeoutError, ConnectionError, OSError) as exc:
                result = FetchResult(
                    [],
                    DataSourceStatus.READ_FAILED,
                    str(exc)[:300],
                    MESSAGE_FETCH_FAILED,
                )
            elapsed_ms = round((self._clock() - started) * 1000)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": result.status.value,
                    "error_type": result.error_type,
                    "elapsed_ms": elapsed_ms,
                }
            )
            result.meta = {
                **(result.meta if isinstance(result.meta, dict) else {}),
                "attempt_count": attempt,
                "attempts": attempts,
                "provider_chain": [self.name],
            }
            last_result = result
            if result.status == DataSourceStatus.OK:
                self._circuit.success()
                return result
            if not self._retryable(result) or attempt >= max_attempts:
                break
            delay = base * (2 ** (attempt - 1))
            delay += delay * 0.2 * self._jitter()
            if delay > 0:
                self._sleep(delay)

        self._circuit.failure(self._clock())
        assert last_result is not None
        return last_result
