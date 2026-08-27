import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config.settings import Settings
from app.db import repository as repo
from app.db.models import Group
from app.sender.base import SendResult
from app.v2.run_store import RunStore
from app.weekly.service import WeeklyInsightsService, previous_natural_week
from app.weekly.store import WeeklyStore


class FakeProvider:
    name = "fake_ai"
    model = "gpt-5.6-sol"

    def __init__(self, calls, *, fail=False):
        self.calls = calls
        self.fail = fail

    def _chat(self, messages, **_kwargs):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("simulated AI failure")
        return "这是只根据聚合统计生成的一次周度叙述。"


class FakeSender:
    def __init__(self):
        self.calls = []

    def send_bundle(self, target, text, image_path):
        self.calls.append((target, text, image_path))
        return (
            SendResult(True, "文字已验证", "2026-08-31T08:30:00+08:00", True, "ui_observed"),
            SendResult(True, "图片已验证", "2026-08-31T08:30:01+08:00", True, "ui_observed"),
        )


def _settings(tmp_path, **updates):
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{(tmp_path / 'weekly.db').as_posix()}",
        **updates,
    )


def _group(settings, *, send=False):
    repo.init_db(settings)
    with Session(repo.engine) as session:
        return repo.save_group(
            session,
            Group(
                display_name="周报群",
                wechat_group_id="weekly@chatroom",
                wechat_group_name="周报当前群名",
                summary_provider="codex",
                summary_model="gpt-5.6-sol",
                wechat_send_enabled=send,
            ),
        )


def _daily(store, group, run_date, *, count, identity, name, topic=""):
    store.update(
        group.display_name,
        run_date,
        group_id=str(group.id),
        status="READY_TO_SEND",
        message_count=count,
        speaker_count=1,
        prompt_meta={
            "topic_selection": {
                "candidates": [
                    {"selected": True, "title": topic, "summary": "已保存摘要"}
                ]
                if topic
                else []
            }
        },
    )
    path = store.ranking_json_path(group.display_name, run_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "message_count": count,
                "speaker_count": 1,
                "top_speakers": [
                    {"rank": 1, "name": name, "count": count, "identity_key": identity}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_previous_natural_week_uses_monday_to_sunday_boundary():
    assert previous_natural_week(date(2026, 8, 31)) == (
        date(2026, 8, 24),
        date(2026, 8, 30),
    )
    assert previous_natural_week(date(2026, 9, 2)) == (
        date(2026, 8, 24),
        date(2026, 8, 30),
    )


def test_weekly_aggregates_saved_daily_artifacts_once_and_preserves_identity(tmp_path):
    settings = _settings(tmp_path)
    group = _group(settings)
    daily_store = RunStore(tmp_path / "output")
    weekly_store = WeeklyStore(daily_store.root)
    _daily(daily_store, group, "2026-08-24", count=3, identity="stable-a", name="旧昵称", topic="项目进展")
    _daily(daily_store, group, "2026-08-25", count=2, identity="stable-a", name="新昵称", topic="项目进展")
    calls = []
    service = WeeklyInsightsService(
        settings,
        daily_store=daily_store,
        weekly_store=weekly_store,
        provider_factory=lambda _settings: FakeProvider(calls),
        sender=FakeSender(),
    )
    now = datetime(2026, 8, 31, 7, 45, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = service.generate_previous_week(now=now)
    second = service.generate_previous_week(now=now)
    state = weekly_store.load("2026-08-24", "2026-08-30", group.id)

    assert first["status"] == "complete"
    assert second["status"] == "complete"
    assert len(calls) == 1
    assert state["aggregation"]["raw_messages_uploaded"] is False
    assert state["aggregation"]["contributors"][0]["identity_key"] == "stable-a"
    assert state["aggregation"]["contributors"][0]["count"] == 5
    assert state["aggregation"]["topics"] == [{"title": "项目进展", "days": 2}]
    assert len(state["aggregation"]["missing_days"]) == 5
    assert weekly_store.card_path("2026-08-24", "2026-08-30", group.id).is_file()


def test_weekly_ai_failure_still_creates_deterministic_text_and_card(tmp_path):
    settings = _settings(tmp_path)
    group = _group(settings)
    daily_store = RunStore(tmp_path / "output")
    weekly_store = WeeklyStore(daily_store.root)
    _daily(daily_store, group, "2026-08-24", count=1, identity="a", name="成员A")
    service = WeeklyInsightsService(
        settings,
        daily_store=daily_store,
        weekly_store=weekly_store,
        provider_factory=lambda _settings: FakeProvider([], fail=True),
        sender=FakeSender(),
    )

    service.generate_previous_week(
        now=datetime(2026, 8, 31, 7, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    state = weekly_store.load("2026-08-24", "2026-08-30", group.id)
    assert state["status"] == "ready_to_send"
    assert state["ai_status"] == "failed"
    assert state["narrative_source"] == "local_deterministic"
    assert "周度洞察" in state["narrative"]


def test_weekly_send_has_independent_state_and_delivery_evidence(tmp_path):
    settings = _settings(tmp_path, weekly_send_enabled=True)
    group = _group(settings, send=True)
    daily_store = RunStore(tmp_path / "output")
    weekly_store = WeeklyStore(daily_store.root)
    _daily(daily_store, group, "2026-08-24", count=2, identity="a", name="成员A")
    sender = FakeSender()
    service = WeeklyInsightsService(
        settings,
        daily_store=daily_store,
        weekly_store=weekly_store,
        provider_factory=lambda _settings: FakeProvider([]),
        sender=sender,
    )
    service.generate_previous_week(
        now=datetime(2026, 8, 31, 7, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    result = service.send_due(
        now=datetime(2026, 8, 31, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    state = weekly_store.load("2026-08-24", "2026-08-30", group.id)

    assert result == [{"group_name": "周报群", "status": "sent"}]
    assert len(sender.calls) == 1
    assert state["status"] == "sent"
    assert state["send_target"] == "周报当前群名"
    assert len(state["text_sha256"]) == len(state["card_sha256"]) == 64
    assert state["send_result"]["verification_level"] == "ui_observed"


def test_stale_weekly_send_claim_becomes_manual_hold_without_resubmit(tmp_path):
    settings = _settings(tmp_path, weekly_send_enabled=True)
    group = _group(settings, send=True)
    daily_store = RunStore(tmp_path / "output")
    weekly_store = WeeklyStore(daily_store.root)
    sender = FakeSender()
    weekly_store.save(
        "2026-08-24",
        "2026-08-30",
        group.id,
        {
            "status": "sending",
            "group_name": group.display_name,
            "send_claim_id": "crashed-claim",
            "send_claim_expires_at": "2026-08-31T08:20:00+08:00",
        },
    )
    service = WeeklyInsightsService(
        settings,
        daily_store=daily_store,
        weekly_store=weekly_store,
        provider_factory=lambda _settings: FakeProvider([]),
        sender=sender,
    )

    result = service.send_due(
        now=datetime(2026, 8, 31, 8, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    state = weekly_store.load("2026-08-24", "2026-08-30", group.id)

    assert result[0]["status"] == "held"
    assert result[0]["error_type"] == "WEEKLY_SEND_RESULT_UNKNOWN"
    assert sender.calls == []
    assert state["status"] == "needs_attention"
    assert state["send_claim_id"] == ""
