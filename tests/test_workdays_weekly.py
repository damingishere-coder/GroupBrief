"""工作日周报闭环：仅使用本地快照和 Fake 外部服务。"""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.ai.prompt_builder_types import PromptOutput
from app.ai.weekly_champion import build_champion
from app.config.settings import Settings
from app.data_sources.base import V2Message, FetchResult, DataSourceStatus
from app.db.models import Group
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.period import PeriodResolver, WORKDAYS_WEEKLY_RULE, automatic_run_allowed, restore_period, next_run_at
from app.v2.run_store import RunStore


def message(index, name="冠军", day=0, kind="text", content="这周研究漫画分镜"):
    return V2Message(str(index), "g@chatroom", "周报测试群", f"wxid_{name}", name,
                     datetime(2026, 8, 31, 10) + timedelta(days=day), kind, content)


class Source:
    name = "fake"

    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def fetch_messages(self, group_id, start, end):
        self.calls.append((start, end))
        return FetchResult([m for m in self.messages if start <= m.timestamp <= end], DataSourceStatus.OK)


class Prompt:
    def __init__(self):
        self.inputs = []
        self.champion_calls = 0

    def _analysis_chat(self, system, text, **kwargs):
        self.champion_calls += 1
        rows = json.loads(text)
        return json.dumps({"message_id": rows[0]["message_id"], "topic": "漫画分镜"}, ensure_ascii=False)

    def build(self, data):
        self.inputs.append(data)
        return PromptOutput(True, prompt="群聊漫画：完整统计数据与真实聊天。", meta={})


class Generator:
    def __init__(self):
        self.calls = []

    def generate(self, prompt_file, output_path):
        from PIL import Image
        from app.image.image_task import ImageTaskResult
        self.calls.append(prompt_file.read_text(encoding="utf-8"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 96)).save(output_path)
        return ImageTaskResult(True, image_path=output_path)


class Sender:
    def __init__(self):
        self.calls = []

    def send_text(self, target, text):
        from app.sender.base import SendResult
        self.calls.append(("text", text))
        return SendResult(True, "", datetime.now().isoformat())

    def send_image(self, target, image_path):
        from app.sender.base import SendResult
        self.calls.append(("image", image_path))
        return SendResult(True, "", datetime.now().isoformat())


@pytest.fixture
def make_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr("app.image.fact_verification.strict_fact_verification_enabled", lambda _: False)
    def make(messages=None):
        group = Group(id=23, display_name="周报测试群", wechat_group_name="周报测试群",
                      wechat_group_id="g@chatroom", enabled=True, image_enabled=True,
                      wechat_send_enabled=True, image_theme="ai_free",
                      ranking_count_policy="text_primary_with_interactions", ranking_template="text_interactions",
                      schedule_rule=WORKDAYS_WEEKLY_RULE)
        settings = Settings(_env_file=None, allow_test_providers=True, summary_provider_primary="deepseek",
                            summary_provider_fallback="", image_generation_concurrency=1,
                            output_root_override=str(tmp_path / "output"))
        source, prompt, generator, sender = Source(messages or [message(1), message(2)]), Prompt(), Generator(), Sender()
        pipeline = DailyPipeline(settings=settings, data_source=source, prompt_builder=prompt,
                                 image_generator=generator, sender=sender, store=RunStore(settings.output_dir))
        monkeypatch.setattr(pipeline, "_load_groups", lambda ids=None: [group] if ids is None or group.id in ids else [])
        monkeypatch.setattr(pipeline, "_get_group", lambda _: group)
        sync_calls = []
        monkeypatch.setattr(pipeline, "_sync_group_names_safe", lambda ids=None: sync_calls.append(ids))
        return SimpleNamespace(pipeline=pipeline, group=group, source=source, prompt=prompt,
                               generator=generator, sender=sender, sync_calls=sync_calls)
    return make


@pytest.mark.parametrize("offset", range(7))
def test_seven_day_schedule(offset):
    target = date(2026, 9, 7) + timedelta(days=offset)
    window = PeriodResolver().resolve(target, schedule_rule=WORKDAYS_WEEKLY_RULE)
    assert window.should_run == (offset < 5)
    assert window.period_end.date() == target - timedelta(days=1)
    assert window.period_start.date() == target - timedelta(days=7 if offset == 0 else 1)
    assert window.top_limit == (15 if offset == 0 else 10)
    assert window.report_kind == ("weekly" if offset == 0 else "daily")


@pytest.mark.parametrize("target,start,end", [("2026-09-07", "2026-08-31", "2026-09-06"), ("2027-01-04", "2026-12-28", "2027-01-03")])
def test_week_crosses_month_and_year(target, start, end):
    window = PeriodResolver().resolve(date.fromisoformat(target), schedule_rule=WORKDAYS_WEEKLY_RULE)
    assert window.period_start_str() == start + " 00:00:00"
    assert window.period_end_str() == end + " 23:59:59"


def test_week_full_data_top15_shared_champion_and_serial_delivery(make_pipeline):
    messages = [message(f"{day}-{i}", name=f"群友{i:02}", day=day) for day in range(7) for i in range(20)]
    messages += [message(f"win-{day}", day=day) for day in range(7) for _ in range(2)]
    messages += [message("winner-extra"), message("interaction", "只发图", kind="image")]
    env = make_pipeline(messages)
    result = env.pipeline.generate_all("2026-09-07")
    assert result[0]["status"] == "ready_to_send", result
    run = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    ranking = json.loads(env.pipeline.store.ranking_json_path(env.group.display_name, "2026-09-07").read_text(encoding="utf-8"))
    assert ranking["top_limit"] == 15 and len(ranking["top_speakers"]) == 15
    assert ranking["top_speakers"][0]["name"] == "冠军"
    assert ranking["top_speakers"][0]["text_count"] == 8  # 重复的 win ID 只计一次
    assert ranking["message_count"] == 149
    greeting = run["weekly_champion"]["text"]
    assert "漫画分镜" in greeting
    text = env.pipeline.store.ranking_txt_path(env.group.display_name, "2026-09-07").read_text(encoding="utf-8")
    assert "本周文字发言排行榜 Top15" in text
    assert text.count(greeting) == 1 and text.index(greeting) < text.index("说明：")
    assert greeting in env.generator.calls[0]
    assert "本周冠军" in env.generator.calls[0] and "顶部主标题下" in env.generator.calls[0]
    assert run["prompt_meta"]["weekly_champion"] == run["weekly_champion"]
    assert env.source.calls == [(datetime(2026, 8, 31), datetime(2026, 9, 6, 23, 59, 59))]
    sent = env.pipeline.send_due(now=datetime(2026, 9, 7, 8, 30))
    assert sent[0]["status"] == "sent"
    assert [call[0] for call in env.sender.calls] == ["text", "image"]
    assert greeting in env.sender.calls[0][1]
    env.pipeline.send_due(now=datetime(2026, 9, 7, 8, 31))
    assert len(env.sender.calls) == 2


def test_retry_reuses_champion_and_refresh_invalidates_it(make_pipeline):
    env = make_pipeline()
    env.pipeline.generate_all("2026-09-07")
    assert env.prompt.champion_calls == 1
    env.pipeline.generate_all("2026-09-07", force=True)
    assert env.prompt.champion_calls == 1
    env.source.messages = [message(3, "新冠军"), message(4, "新冠军")]
    env.pipeline.force_generate(env.group.id, "2026-09-07", refresh_messages=True)
    run = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    assert run["weekly_champion"] == {} and run["send_hold"]
    assert env.prompt.champion_calls == 1
    env.pipeline.rebuild_prompt_from_snapshot(env.group.id, "2026-09-07", allow_topic_reselection=True)
    updated = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    assert updated["weekly_champion"]["name"] == "新冠军"
    assert env.prompt.champion_calls == 2
    assert env.prompt.inputs[-1].report_kind == "weekly"
    assert env.prompt.inputs[-1].period_start == "2026-08-31 00:00:00"


@pytest.mark.parametrize("now,target", [("2026-09-05T09:00:00", "2026-09-04"), ("2026-09-06T09:00:00", "2026-09-05"), ("2026-09-07T09:00:00", "2026-09-06")])
def test_automatic_backfill_and_send_block_before_external_work(make_pipeline, now, target):
    env = make_pipeline()
    env.pipeline.store.update(env.group.display_name, target, status="READY_TO_SEND", image_enabled=True,
                              group_id=str(env.group.id), wechat_send_enabled=True)
    result = env.pipeline.generate_all(target, automatic_now=datetime.fromisoformat(now))
    assert result[0]["status"] == "no_groups"
    assert env.pipeline.send_due_for_dates([target], now=datetime.fromisoformat(now), recovery=True) == []
    assert env.source.calls == env.prompt.inputs == env.generator.calls == env.sender.calls == env.sync_calls == []


def test_monday_blocks_legacy_single_day_ready_snapshot(make_pipeline):
    env = make_pipeline()
    env.pipeline.store.update(env.group.display_name, "2026-09-07", status="READY_TO_SEND",
                              period_start="2026-09-06 00:00:00", period_end="2026-09-06 23:59:59")
    assert env.pipeline.send_due(now=datetime(2026, 9, 7, 8, 30)) == []
    assert not env.sync_calls


def test_no_text_has_no_champion(make_pipeline):
    env = make_pipeline([message(1, kind="image")])
    env.pipeline.generate_all("2026-09-07")
    run = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    assert run["weekly_champion"] == {}
    assert not env.prompt.champion_calls
    assert "本周冠军" not in env.generator.calls[0]


def test_champion_rejects_invented_topics_and_unknown_call():
    seed = {"name": "小王", "text": "恭喜 小王 获得本周文字发言第一名！",
            "evidence": [{"message_id": "1", "text": "研究漫画分镜"}]}
    result = build_champion(seed, lambda *a, **kw: '{"message_id":"1","topic":"环球旅行"}')
    assert result["text"] == seed["text"] and result["evidence"] == []
    def fail(*args, **kwargs):
        raise RuntimeError("unknown")
    assert build_champion(seed, fail)["text"] == seed["text"]


def test_old_snapshot_period_is_not_reinterpreted_and_next_time_skips_weekend():
    window = PeriodResolver().resolve(date(2026, 9, 7), schedule_rule=WORKDAYS_WEEKLY_RULE)
    restored = restore_period(window, {"period_start": "2026-09-06 00:00:00", "period_end": "2026-09-06 23:59:59"})
    assert restored.report_kind == "daily" and restored.top_limit == 10
    assert automatic_run_allowed("daily_previous_day", date(2026, 9, 6), datetime(2026, 9, 6))
    assert next_run_at(datetime(2026, 9, 4, 9, tzinfo=ZoneInfo("Asia/Shanghai")), "00:15", [WORKDAYS_WEEKLY_RULE]) == "2026-09-07T00:15:00+08:00"


@pytest.mark.parametrize("offset", range(7))
def test_week_of_real_pipeline_entrypoints(make_pipeline, offset):
    env = make_pipeline([message(i, day=i) for i in range(14)])
    target = date(2026, 9, 7) + timedelta(days=offset)
    env.pipeline.generate_all(target.isoformat(), automatic_now=datetime.combine(target, datetime.min.time()).replace(hour=1))
    if offset >= 5:
        assert not env.source.calls and not env.generator.calls and not env.sync_calls
        return
    run = env.pipeline.store.load_run(env.group.display_name, target.isoformat())
    assert run["top_limit"] == (15 if offset == 0 else 10)
    assert bool(run.get("weekly_champion")) == (offset == 0)
    assert env.source.calls[0][0].date() == target - timedelta(days=7 if offset == 0 else 1)
    env.pipeline.send_due(now=datetime.combine(target, datetime.min.time()).replace(hour=8, minute=30))
    assert [call[0] for call in env.sender.calls] == ["text", "image"]


def test_interrupted_champion_call_falls_back_without_resubmission(make_pipeline):
    env = make_pipeline()
    env.pipeline.generate_all("2026-09-07")
    run = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    env.pipeline.store.update(env.group.display_name, "2026-09-07",
                              weekly_champion={**run["weekly_champion"], "status": "building"})
    env.pipeline.generate_all("2026-09-07", force=True)
    updated = env.pipeline.store.load_run(env.group.display_name, "2026-09-07")
    assert env.prompt.champion_calls == 1
    assert updated["weekly_champion"]["source"] == "local_deterministic"
    assert updated["weekly_champion"]["error_type"] == "CHAMPION_RESULT_UNKNOWN"


def test_monday_old_manifest_cannot_override_current_rule(make_pipeline):
    env = make_pipeline()
    old_manifest = {23: {"schedule_rule": "daily_previous_day", "period_start": "2026-09-06T00:00:00", "period_end": "2026-09-06T23:59:59"}}
    results = env.pipeline.generate_all("2026-09-07", group_overrides=old_manifest, automatic_now=datetime(2026, 9, 7, 1))
    assert results[0]["status"] == "no_groups"
    assert not env.source.calls and not env.sync_calls


def test_edited_prompt_cannot_drop_champion_before_image_or_send(make_pipeline):
    env = make_pipeline()
    env.pipeline.generate_all("2026-09-07")
    env.pipeline.store.prompt_path(env.group.display_name, "2026-09-07").write_text("手动删掉庆祝内容", encoding="utf-8")
    with pytest.raises(ValueError, match="冠军祝贺不一致"):
        env.pipeline._make_image_job(env.group, "2026-09-07", force=True)
    results = env.pipeline.send_due(now=datetime(2026, 9, 7, 8, 30))
    assert results[0]["error_type"] == "WEEKLY_CHAMPION_MISMATCH"
    assert not env.sender.calls


def test_scheduler_and_reconcile_skip_weekend_before_generation(make_pipeline, monkeypatch):
    from app.scheduler import daily_v2_job as daily
    env = make_pipeline()
    monkeypatch.setattr(daily, "DailyPipeline", lambda **kwargs: env.pipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: None)
    state = daily.DailyScheduleState(env.pipeline.settings.output_dir)
    state.update("2026-09-04", generation_completed_at="2026-09-04T01:00:00", generation_status="partial")
    result = daily.run_daily_v2_job("2026-09-04", settings=env.pipeline.settings, now=datetime(2026, 9, 5, 1))
    assert result["status"] == "not_run"
    assert not env.source.calls and not env.generator.calls
    assert state.load("2026-09-04")["generation_status"] == "partial"


def test_mixed_manifest_does_not_seal_deferred_groups(make_pipeline, monkeypatch):
    from app.scheduler import daily_v2_job as daily
    env = make_pipeline()
    other = env.group.model_copy(update={"id": 24, "display_name": "旧规则群", "schedule_rule": "daily_previous_day"})
    monkeypatch.setattr(env.pipeline, "_load_groups", lambda: [env.group, other])
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return [{"status": "ready_to_send", "group_name": other.display_name}]
    monkeypatch.setattr(env.pipeline, "generate_all", generate)
    monkeypatch.setattr(daily, "DailyPipeline", lambda **kwargs: env.pipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: None)
    result = daily.run_daily_v2_job("2026-09-04", settings=env.pipeline.settings, now=datetime(2026, 9, 5, 1))
    assert calls[0]["group_ids"] == [24]
    state = daily.DailyScheduleState(env.pipeline.settings.output_dir).load("2026-09-04")
    assert not state.get("generation_completed_at")
    assert any(row.get("error_type") == "SCHEDULE_POLICY_DEFERRED" for row in state["generation_results"])
    assert result["status"] != "success"


def test_cutover_preview_backup_and_scope(tmp_path):
    import sqlite3
    from scripts.configure_workdays_weekly import configure, TARGETS
    db = tmp_path / "groups.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE groups (id INTEGER PRIMARY KEY, wechat_group_id TEXT, display_name TEXT, enabled INTEGER, schedule_rule TEXT, updated_at TEXT)")
        connection.executemany("INSERT INTO groups VALUES (?, ?, '群', 1, 'daily_previous_day', '')", list(TARGETS.items()) + [(99, "unrelated")])
    before = db.read_bytes()
    assert configure(db)["applied"] is False
    assert db.read_bytes() == before
    result = configure(db, apply=True)
    assert result["applied"]
    with sqlite3.connect(result["backup"]) as backup:
        assert backup.execute("SELECT DISTINCT schedule_rule FROM groups").fetchall() == [("daily_previous_day",)]
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM groups WHERE schedule_rule=?", (WORKDAYS_WEEKLY_RULE,)).fetchone()[0] == 6
        assert connection.execute("SELECT schedule_rule FROM groups WHERE id=99").fetchone()[0] == "daily_previous_day"
    assert configure(db, apply=True)["applied"] is False
