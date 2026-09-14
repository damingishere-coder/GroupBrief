import json
from datetime import datetime, timedelta

import pytest

from app.ai.speaker_attribution import build_attribution_contract
from app.data_sources.base import DataSourceStatus, FetchResult, V2Message
from app.pipeline.daily_pipeline import DailyPipeline
from app.pipeline.weekly_messages import fetch_weekly_messages
from app.v2.run_store import RunStore


def message(day, group_id="g@chatroom"):
    return V2Message(str(day), group_id, "群", "user", "成员", datetime(2026, 9, day, 12), content="消息")


def snapshot(store, day, mutation=None):
    m = message(day)
    run_date = (m.timestamp.date() + timedelta(days=1)).isoformat()
    path = store.messages_path("群", run_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([m.to_dict()], ensure_ascii=False), encoding="utf-8")
    state = dict(report_kind="daily", wechat_group_id=m.group_id,
                 message_snapshot_period_start=f"2026-09-{day:02d} 00:00:00",
                 message_snapshot_period_end=f"2026-09-{day:02d} 23:59:59",
                 message_snapshot_sha256=build_attribution_contract([m]).message_snapshot_sha256)
    if mutation:
        state.update(mutation)
    store.update("群", run_date, **state)
    return path


class Source:
    def __init__(self, fail=False, empty=False):
        self.calls = []
        self.fail = fail
        self.empty = empty

    def fetch_messages(self, group_id, start, end):
        self.calls.append((start, end))
        if self.fail:
            return FetchResult([], DataSourceStatus.READ_FAILED, "timeout", "MESSAGE_FETCH_FAILED")
        if self.empty:
            return FetchResult([], DataSourceStatus.EMPTY_RESULT)
        return FetchResult([message(day) for day in range(start.day, end.day + 1)], meta={"mcp_call_count": 3})


def fetch(store, source):
    return fetch_weekly_messages(store=store, group_name="群", group_id="g@chatroom",
                                 start=datetime(2026, 9, 7), end=datetime(2026, 9, 13, 23, 59, 59),
                                 data_source=source, load_snapshot=DailyPipeline._load_message_snapshot,
                                 timezone="Asia/Shanghai")


def test_reuses_four_days_and_fetches_only_missing_weekend(tmp_path):
    store = RunStore(tmp_path)
    originals = {}
    for day in range(7, 11):
        path = snapshot(store, day)
        originals[path] = path.read_bytes()
    source = Source()
    result = fetch(store, source)
    assert result.status == DataSourceStatus.OK
    assert [m.message_id for m in result.messages] == [str(day) for day in range(7, 14)]
    assert source.calls == [(datetime(2026, 9, 11), datetime(2026, 9, 13, 23, 59, 59))]
    assert result.meta["snapshot_message_count"] == 4
    assert result.meta["mcp_call_count"] == 3
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_complete_snapshots_need_no_upstream_calls(tmp_path):
    store = RunStore(tmp_path)
    for day in range(7, 14):
        snapshot(store, day)
    source = Source(fail=True)
    assert len(fetch(store, source).messages) == 7
    assert source.calls == []


@pytest.mark.parametrize("mutation", [
    {"message_snapshot_sha256": "bad"},
    {"wechat_group_id": "other@chatroom"},
    {"message_snapshot_period_end": "2026-09-07 12:00:00"},
    {"report_kind": "weekly"},
])
def test_invalid_snapshot_does_not_claim_coverage(tmp_path, mutation):
    store = RunStore(tmp_path)
    snapshot(store, 7, mutation)
    source = Source()
    result = fetch(store, source)
    assert len(result.messages) == 7
    assert source.calls[0][0] == datetime(2026, 9, 7)
    assert result.meta["snapshot_message_count"] == 0
    assert len(result.meta["snapshot_rejected"]) == 1


def test_failed_gap_never_returns_partial_week(tmp_path):
    store = RunStore(tmp_path)
    snapshot(store, 7)
    result = fetch(store, Source(fail=True))
    assert result.status == DataSourceStatus.READ_FAILED
    assert result.messages == []


def test_confirmed_empty_gap_keeps_cached_messages(tmp_path):
    store = RunStore(tmp_path)
    snapshot(store, 7)
    result = fetch(store, Source(empty=True))
    assert result.status == DataSourceStatus.OK
    assert [m.message_id for m in result.messages] == ["7"]
