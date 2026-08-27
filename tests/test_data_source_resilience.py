from datetime import datetime

from app.config.settings import Settings
from app.data_sources.base import (
    DataSourceHealth,
    DataSourceStatus,
    FetchResult,
    ResolvedGroup,
    V2Message,
    WeChatDataSource,
)
from app.data_sources.resilient import ProviderCircuitBreaker, ResilientWeChatDataSource


def _message() -> V2Message:
    return V2Message(
        message_id="m1",
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id="u1",
        sender_name="成员甲",
        timestamp=datetime(2026, 8, 27, 10, 0),
        content="hello",
    )


class ScriptedSource(WeChatDataSource):
    name = "scripted"

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def health_check(self):
        return DataSourceHealth(DataSourceStatus.OK, "ok")

    def list_groups(self):
        return [ResolvedGroup("g1@chatroom", "测试群")]

    def resolve_group(self, group_name):
        return self.list_groups()

    def fetch_messages(self, group_id, start_time, end_time):
        self.calls += 1
        return self.results[min(self.calls - 1, len(self.results) - 1)]


def test_transient_fetch_failure_retries_same_provider_then_succeeds():
    failures = [
        FetchResult([], DataSourceStatus.READ_FAILED, "timeout", "MESSAGE_FETCH_FAILED"),
        FetchResult([], DataSourceStatus.UNAVAILABLE, "down", "WECHAT_DATA_UNAVAILABLE"),
        FetchResult([_message()], DataSourceStatus.OK, "ok"),
    ]
    source = ScriptedSource(failures)
    sleeps = []
    settings = Settings(
        _env_file=None,
        wechat_fetch_max_attempts=3,
        wechat_fetch_retry_backoff_seconds=1,
    )
    resilient = ResilientWeChatDataSource(
        source,
        settings,
        sleep=sleeps.append,
        jitter=lambda: 0,
    )

    result = resilient.fetch_messages("g1@chatroom", datetime(2026, 8, 27), datetime(2026, 8, 28))

    assert result.status == DataSourceStatus.OK
    assert source.calls == 3
    assert sleeps == [1, 2]
    assert result.meta["attempt_count"] == 3
    assert result.meta["provider_chain"] == ["scripted"]


def test_group_not_found_is_never_retried():
    source = ScriptedSource(
        [FetchResult([], DataSourceStatus.GROUP_NOT_FOUND, "missing", "GROUP_NOT_FOUND")]
    )
    resilient = ResilientWeChatDataSource(
        source,
        Settings(_env_file=None, wechat_fetch_max_attempts=3),
        sleep=lambda _seconds: None,
    )

    result = resilient.fetch_messages("missing", datetime(2026, 8, 27), datetime(2026, 8, 28))

    assert result.status == DataSourceStatus.GROUP_NOT_FOUND
    assert source.calls == 1


def test_circuit_opens_and_half_open_probe_can_recover():
    clock = [100.0]
    source = ScriptedSource(
        [
            FetchResult([], DataSourceStatus.READ_FAILED, "down", "MESSAGE_FETCH_FAILED"),
            FetchResult([_message()], DataSourceStatus.OK, "ok"),
        ]
    )
    settings = Settings(
        _env_file=None,
        wechat_fetch_max_attempts=1,
        wechat_fetch_circuit_failure_threshold=1,
        wechat_fetch_circuit_cooldown_seconds=10,
    )
    resilient = ResilientWeChatDataSource(
        source,
        settings,
        sleep=lambda _seconds: None,
        clock=lambda: clock[0],
    )

    first = resilient.fetch_messages("g1", datetime(2026, 8, 27), datetime(2026, 8, 28))
    blocked = resilient.fetch_messages("g1", datetime(2026, 8, 27), datetime(2026, 8, 28))
    clock[0] += 10
    recovered = resilient.fetch_messages("g1", datetime(2026, 8, 27), datetime(2026, 8, 28))

    assert first.status == DataSourceStatus.READ_FAILED
    assert blocked.meta["circuit_open"] is True
    assert recovered.status == DataSourceStatus.OK
    assert source.calls == 2


def test_half_open_circuit_allows_only_one_probe_until_result():
    circuit = ProviderCircuitBreaker(threshold=1, cooldown_seconds=10)
    circuit.failure(100.0)

    assert circuit.allow(110.0) is True
    assert circuit.allow(110.0) is False

    circuit.success()
    assert circuit.allow(110.0) is True
