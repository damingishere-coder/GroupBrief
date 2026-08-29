"""V2 P7：DailyPipeline 集成测试。

注入 Fake 数据源/DeepSeek/生图/发送，隔离所有外部依赖，验证：
状态机推进 / 文件生成 / 防重复 / force / 失败隔离 / 生图串行 /
发送到点 / 不重复发送 / 周六跳过。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.data_sources.base import (
    DataSourceHealth,
    DataSourceStatus,
    FetchResult,
    ResolvedGroup,
    V2Message,
    WeChatDataSource,
)
from sqlmodel import Session

from app.db import repository as repo
from app.db.models import Group, GroupRun, Report
from app.pipeline.daily_pipeline import DailyPipeline
from app.v2.constants import (
    FAILED,
    IMAGE_FILE_MISSING,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    PROMPT_READY,
    READY_TO_SEND,
    SENT,
)
from app.v2.run_store import RunStore


def _clear_groups() -> None:
    with Session(repo.engine) as session:
        # 测试数据库需要真正清空；先按外键依赖顺序删除历史关系。
        session.exec(Report.__table__.delete())
        session.exec(GroupRun.__table__.delete())
        session.exec(Group.__table__.delete())
        session.commit()


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    """初始化测试数据库（conftest 已指向 test_groupbrief.db）。

    模块开始与结束都清空群表，避免测试库状态残留破坏其他测试文件。
    """
    from app.config.settings import get_settings

    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)
    _clear_groups()
    yield
    _clear_groups()


def _msg(sender: str, content: str = "hi", i: int = 0) -> V2Message:
    return V2Message(
        message_id=f"m{i}",
        group_id="g@chatroom",
        group_name="测试群",
        sender_id=f"wxid_{sender}",
        sender_name=sender,
        timestamp=datetime(2026, 8, 17, 10, 30, 0),
        message_type="text",
        content=content,
    )


class FakeSource(WeChatDataSource):
    name = "fake"

    def __init__(self, messages=None, fail=False, error_type="", group_name="测试群", available=True):
        self.messages = messages or [_msg("张三", "今天聊了票房", i=1), _msg("李四", "《牛来》破500万", i=2)]
        self.fail = fail
        self.error_type = error_type
        self.group_name = group_name
        self.available = available
        self.fetch_calls = 0

    def health_check(self) -> DataSourceHealth:
        if self.available:
            return DataSourceHealth(DataSourceStatus.OK, "ok")
        return DataSourceHealth(DataSourceStatus.UNAVAILABLE, "unavailable")

    def list_groups(self) -> list[ResolvedGroup]:
        return [ResolvedGroup(group_id="g@chatroom", group_name=self.group_name)]

    def resolve_group(self, name: str) -> list[ResolvedGroup]:
        return []

    def fetch_messages(self, group_id, start_time, end_time) -> FetchResult:
        self.fetch_calls += 1
        if self.fail:
            return FetchResult([], DataSourceStatus.READ_FAILED, "取数失败", self.error_type or "MESSAGE_FETCH_FAILED")
        return FetchResult(self.messages, DataSourceStatus.OK, "ok")


class FakePrompt:
    def __init__(self, fail=False, failure_meta=None):
        self.fail = fail
        self.failure_meta = failure_meta
        self.inputs = []

    def build(self, data):
        from app.ai.prompt_builder_types import PromptOutput

        self.inputs.append(data)
        if self.fail:
            return PromptOutput(
                False,
                error="DeepSeek 失败",
                meta=self.failure_meta,
            )
        topic_selection = data.persisted_topic_selection or {
            "topic_selection_version": "4.0",
            "selected_topic_ids": ["topic-01", "topic-02"],
            "selected_count": 2,
            "candidates": [
                {
                    "topic_id": "topic-01", "selected": True, "title": "票房",
                    "summary": "张三聊票房", "message_ids": ["m1"],
                    "quotes": ["今天聊了票房"], "visible_participants": ["张三"],
                },
                {
                    "topic_id": "topic-02", "selected": True, "title": "牛来",
                    "summary": "李四回应票房", "message_ids": ["m2"],
                    "quotes": ["《牛来》破500万"], "visible_participants": ["李四"],
                },
            ],
        }
        return PromptOutput(
            True,
            "【任务】\n生成图片\n【主标题】今天热聊",
            meta={
                "mode": "persisted_topic_selection" if data.persisted_topic_selection else "single",
                "topic_selection": topic_selection,
                "layout_catalog_version": "comic-panels-v3",
                "layout_id": "split_focus",
                "structure_mode": "dual_rhythm",
                "featured_topic_ids": ["topic-01", "topic-02"],
                "topic_order": ["topic-01", "topic-02"],
                "panel_beats": [
                    {"topic_id": "topic-01", "shots": ["establishing", "reaction"]},
                    {"topic_id": "topic-02", "shots": ["dialogue"]},
                ],
            },
        )


class FakeGenerator:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[Path] = []

    def generate(self, prompt_file: Path, output_path: Path):
        from app.image.image_task import ImageTaskResult

        self.calls.append(output_path)
        if self.fail:
            return ImageTaskResult(False, error="生图失败")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image

        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(output_path, format="PNG")
        return ImageTaskResult(True, image_path=output_path)


class FakeSender:
    def __init__(self, fail_text=False, fail_image=False):
        self.fail_text = fail_text
        self.fail_image = fail_image
        self.text_calls: list[tuple[str, str]] = []
        self.image_calls: list[tuple[str, Path]] = []

    def health_check(self):
        return True, "ok"

    def send_text(self, target: str, text: str):
        from app.sender.base import SendResult

        self.text_calls.append((target, text))
        return SendResult(not self.fail_text, "" if not self.fail_text else "文字发送失败", datetime.now().isoformat())

    def send_image(self, target: str, image_path):
        from app.sender.base import SendResult

        self.image_calls.append((target, Path(image_path)))
        return SendResult(not self.fail_image, "" if not self.fail_image else "图片发送失败", datetime.now().isoformat())


class UnknownTextSender(FakeSender):
    def send_text(self, target: str, text: str):
        from app.sender.base import SendResult

        self.text_calls.append((target, text))
        return SendResult(
            False,
            "已按 Enter，但未观察到 UI 变化",
            datetime.now().isoformat(),
            submitted=True,
            verification_level="unknown",
            outcome_unknown=True,
        )


class SubmittedFailureTextSender(FakeSender):
    def send_text(self, target: str, text: str):
        from app.sender.base import SendResult

        self.text_calls.append((target, text))
        return SendResult(
            False,
            "提交后提供方返回失败",
            datetime.now().isoformat(),
            submitted=True,
            verification_level="unknown",
            outcome_unknown=False,
        )


def _make_pipeline(tmp_path, source=None, prompt=None, gen=None, sender=None, image_enabled=True, send_time="08:30", image_theme="blue_white", image_theme_custom="", schedule_rule="daily_previous_day"):
    source = source or FakeSource()
    prompt = prompt or FakePrompt()
    gen = gen or FakeGenerator()
    sender = sender or FakeSender()
    store = RunStore(tmp_path / "output")

    # 创建/复用名为"测试群"的群（幂等，避免误取测试库残留群）
    from sqlmodel import select

    with Session(repo.engine) as session:
        group = session.exec(select(Group).where(Group.display_name == "测试群")).first()
        if group is None:
            group = Group(
                display_name="测试群",
                wechat_group_id="g@chatroom",
                wechat_group_name="测试群",
                enabled=True,
                schedule_rule=schedule_rule,
                send_time=send_time,
                image_enabled=image_enabled,
                image_theme=image_theme,
                image_theme_custom=image_theme_custom,
                wechat_send_enabled=True,
            )
        else:
            group.wechat_group_id = "g@chatroom"
            group.wechat_group_name = "测试群"
            group.send_target = ""
            group.schedule_rule = schedule_rule
            group.image_enabled = image_enabled
            group.send_time = send_time
            group.image_theme = image_theme
            group.image_theme_custom = image_theme_custom
            group.wechat_send_enabled = True
        group = repo.save_group(session, group)

    return DailyPipeline(
        data_source=source,
        prompt_builder=prompt,
        image_generator=gen,
        sender=sender,
        store=store,
    ), group


# ---------- 生成阶段 ----------


def test_generate_flow_reaches_ready_to_send(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    results = pipeline.generate_all(run_date="2026-08-18")  # 周二
    assert results[0]["status"] == "ready_to_send"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    # 文件生成
    assert pipeline.store.messages_path("测试群", "2026-08-18").exists()
    assert pipeline.store.ranking_json_path("测试群", "2026-08-18").exists()
    assert pipeline.store.ranking_txt_path("测试群", "2026-08-18").exists()
    assert pipeline.store.prompt_path("测试群", "2026-08-18").exists()
    assert pipeline.store.image_path("测试群", "2026-08-18").exists()
    assert run["imagegen_ms"] >= 0
    assert run["stage_timings"]["imagegen_ms"] == run["imagegen_ms"]
    assert run["image_size_bytes"] == pipeline.store.image_path("测试群", "2026-08-18").stat().st_size
    assert run["image_generated_at"]


def test_generate_skip_when_already_ready(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    source = FakeSource()  # 重新计数
    pipeline2, _ = _make_pipeline(tmp_path, source=source)
    results = pipeline2.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "skipped"


def test_historical_recovery_requires_confirmation_and_never_auto_sends(tmp_path):
    pipeline, group = _make_pipeline(tmp_path, send_time="08:30")
    pipeline.generate_all(run_date="2026-08-18")
    sender = FakeSender()
    pipeline.sender = sender
    now = datetime(2026, 8, 19, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    first = pipeline.send_due_for_dates(["2026-08-18"], now=now, recovery=True)
    second = pipeline.send_due_for_dates(["2026-08-18"], now=now, recovery=True)

    assert first[0]["status"] == "held"
    assert first[0]["error_type"] == "HISTORICAL_SEND_REQUIRES_CONFIRMATION"
    assert second == []
    assert sender.text_calls == []
    assert sender.image_calls == []
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["send_hold_reason"] == "HISTORICAL_SEND_REQUIRES_CONFIRMATION"


def test_generate_force_regenerates(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    source = FakeSource()
    pipeline2, _ = _make_pipeline(tmp_path, source=source)
    results = pipeline2.generate_all(run_date="2026-08-18", force=True)
    assert results[0]["status"] == "ready_to_send"
    assert source.fetch_calls == 0
    run = pipeline2.store.load_run("测试群", "2026-08-18")
    assert run["message_snapshot_reused"] is True
    assert run["message_snapshot_refreshed"] is False


def test_generate_explicit_refresh_replaces_saved_snapshot(tmp_path):
    pipeline, _ = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    prompt_path = pipeline.store.prompt_path("测试群", "2026-08-18")
    image_path = pipeline.store.image_path("测试群", "2026-08-18")
    prompt_before = prompt_path.read_bytes()
    image_before = image_path.read_bytes()
    run_before = pipeline.store.load_run("测试群", "2026-08-18")
    prompt_meta_before = run_before["prompt_meta"]

    refreshed = FakeSource(messages=[_msg("王五", "这是显式刷新的消息", i=9)])
    prompt = FakePrompt()
    generator = FakeGenerator()
    pipeline2, _ = _make_pipeline(tmp_path, source=refreshed, prompt=prompt, gen=generator)
    results = pipeline2.generate_all(
        run_date="2026-08-18",
        refresh_messages=True,
    )

    assert results[0]["status"] == "data_ready"
    assert refreshed.fetch_calls == 1
    assert prompt.inputs == []
    assert generator.calls == []
    assert prompt_path.read_bytes() == prompt_before
    assert image_path.read_bytes() == image_before
    saved = json.loads(
        pipeline2.store.messages_path("测试群", "2026-08-18").read_text(encoding="utf-8")
    )
    assert [item["content"] for item in saved] == ["这是显式刷新的消息"]
    run = pipeline2.store.load_run("测试群", "2026-08-18")
    ranking = json.loads(
        pipeline2.store.ranking_json_path("测试群", "2026-08-18").read_text(encoding="utf-8")
    )
    assert run["message_snapshot_reused"] is False
    assert run["message_snapshot_refreshed"] is True
    assert run["message_count"] == ranking["message_count"] == 1
    assert run["speaker_count"] == ranking["speaker_count"] == 1
    assert run["prompt_meta"] == prompt_meta_before
    assert run["prompt_rebuild_status"] == "required"
    assert run["send_hold"] is True


def test_refresh_preserves_sent_status_and_sent_at_without_prompt_image_or_send(tmp_path):
    pipeline, _ = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    sent_at = "2026-08-18T08:31:00+08:00"
    pipeline.store.update("测试群", "2026-08-18", status=SENT, sent_at=sent_at)

    refreshed = FakeSource(messages=[_msg("同名", i=1), _msg("同名", i=2)])
    prompt = FakePrompt()
    generator = FakeGenerator()
    sender = FakeSender()
    pipeline2, _ = _make_pipeline(
        tmp_path, source=refreshed, prompt=prompt, gen=generator, sender=sender
    )
    result = pipeline2.generate_all(run_date="2026-08-18", refresh_messages=True)
    run = pipeline2.store.load_run("测试群", "2026-08-18")

    assert result[0]["status"] == "data_ready"
    assert run["status"] == SENT
    assert run["sent_at"] == sent_at
    assert prompt.inputs == []
    assert generator.calls == []
    assert sender.text_calls == []
    assert sender.image_calls == []


def test_rebuild_prompt_uses_saved_snapshot_without_fetch_or_image(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    sent_at = "2026-08-18T09:00:00+08:00"
    pipeline.store.update("测试群", "2026-08-18", status=SENT, sent_at=sent_at)
    snapshot_path = pipeline.store.messages_path("测试群", "2026-08-18")
    image_path = pipeline.store.image_path("测试群", "2026-08-18")
    snapshot_before = snapshot_path.read_bytes()
    image_before = image_path.read_bytes()

    source = FakeSource(fail=True)
    prompt = FakePrompt()
    generator = FakeGenerator()
    pipeline2, _ = _make_pipeline(tmp_path, source=source, prompt=prompt, gen=generator)
    result = pipeline2.rebuild_prompt_from_snapshot(group.id, "2026-08-18")

    assert result["status"] == "prompt_ready"
    assert source.fetch_calls == 0
    assert len(prompt.inputs) == 1
    assert prompt.inputs[0].persisted_topic_selection["selected_topic_ids"] == ["topic-01", "topic-02"]
    assert generator.calls == []
    assert snapshot_path.read_bytes() == snapshot_before
    assert image_path.read_bytes() == image_before
    run = pipeline2.store.load_run("测试群", "2026-08-18")
    assert run["status"] == SENT
    assert run["sent_at"] == sent_at
    assert run["message_snapshot_reused"] is True
    assert run["prompt_rebuild_status"] == "ready_for_review"
    assert run["image_regen_status"] == "prompt_rebuilt"
    assert run["send_hold"] is True


def test_rebuild_failure_preserves_sent_status_and_sent_at(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    sent_at = "2026-08-18T09:00:00+08:00"
    pipeline.store.update("测试群", "2026-08-18", status=SENT, sent_at=sent_at)

    pipeline2, _ = _make_pipeline(
        tmp_path,
        source=FakeSource(fail=True),
        prompt=FakePrompt(fail=True),
        gen=FakeGenerator(),
    )
    result = pipeline2.rebuild_prompt_from_snapshot(group.id, "2026-08-18")
    run = pipeline2.store.load_run("测试群", "2026-08-18")

    assert result["status"] == "failed"
    assert pipeline2.data_source.fetch_calls == 0
    assert run["status"] == SENT
    assert run["sent_at"] == sent_at
    assert run["prompt_rebuild_status"] == "failed"
    assert run["send_hold"] is True


def test_rebuild_missing_topics_requires_explicit_reselection_and_preserves_user_hold(
    tmp_path,
):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    sent_at = "2026-08-18T09:00:00+08:00"
    pipeline.store.update(
        "测试群",
        "2026-08-18",
        status=SENT,
        sent_at=sent_at,
        prompt_meta={"mode": "local_infographic", "fallback_level": 3},
        send_hold=True,
        send_hold_reason="USER_REQUEST_NO_SEND_2026_08_18",
        needs_manual_send=True,
        image_force_local_fallback=True,
    )

    source = FakeSource(fail=True)
    prompt = FakePrompt()
    generator = FakeGenerator()
    pipeline2, _ = _make_pipeline(
        tmp_path,
        source=source,
        prompt=prompt,
        gen=generator,
    )

    blocked = pipeline2.rebuild_prompt_from_snapshot(group.id, "2026-08-18")
    rebuilt = pipeline2.rebuild_prompt_from_snapshot(
        group.id,
        "2026-08-18",
        allow_topic_reselection=True,
    )
    run = pipeline2.store.load_run("测试群", "2026-08-18")

    assert blocked["error_type"] == "TOPIC_SELECTION_SNAPSHOT_INVALID"
    assert rebuilt["status"] == "prompt_ready"
    assert source.fetch_calls == 0
    assert len(prompt.inputs) == 1
    assert prompt.inputs[0].persisted_topic_selection is None
    assert generator.calls == []
    assert run["status"] == SENT
    assert run["sent_at"] == sent_at
    assert run["prompt_topic_reselected"] is True
    assert run["prompt_meta"]["topic_selection"]["selected_count"] == 2
    assert run["image_force_local_fallback"] is False
    assert run["send_hold"] is True
    assert run["needs_manual_send"] is True
    assert run["send_hold_reason"] == "USER_REQUEST_NO_SEND_2026_08_18"


def test_generate_corrupt_snapshot_fails_without_hidden_refetch(tmp_path):
    source = FakeSource()
    pipeline, _ = _make_pipeline(tmp_path, source=source)
    path = pipeline.store.messages_path("测试群", "2026-08-18")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    results = pipeline.generate_all(run_date="2026-08-18", force=True)

    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "MESSAGE_SNAPSHOT_INVALID"
    assert source.fetch_calls == 0
    assert path.read_text(encoding="utf-8") == "{broken"


def test_monday_and_tuesday_both_generate_top10(tmp_path):
    messages = [_msg(f"成员{n:02}", i=n) for n in range(20)]
    pipeline, _ = _make_pipeline(
        tmp_path,
        source=FakeSource(messages=messages),
        image_enabled=False,
    )

    monday = pipeline.generate_all(run_date="2026-08-17")
    assert monday[0]["status"] == "ready_to_send"
    monday_json = json.loads(
        pipeline.store.ranking_json_path("测试群", "2026-08-17").read_text(encoding="utf-8")
    )
    monday_text = pipeline.store.ranking_txt_path(
        "测试群", "2026-08-17"
    ).read_text(encoding="utf-8")
    assert monday_json["top_limit"] == 10
    assert len(monday_json["top_speakers"]) == 10
    assert monday_json["top_speakers"][-1]["rank"] == 10
    assert "发言 Top10" in monday_text

    tuesday = pipeline.generate_all(run_date="2026-08-18")
    assert tuesday[0]["status"] == "ready_to_send"
    tuesday_json = json.loads(
        pipeline.store.ranking_json_path("测试群", "2026-08-18").read_text(encoding="utf-8")
    )
    tuesday_text = pipeline.store.ranking_txt_path(
        "测试群", "2026-08-18"
    ).read_text(encoding="utf-8")
    assert tuesday_json["top_limit"] == 10
    assert len(tuesday_json["top_speakers"]) == 10
    assert "发言 Top10" in tuesday_text


def test_pipeline_passes_group_theme_and_records_request_metadata(tmp_path):
    prompt = FakePrompt()
    pipeline, group = _make_pipeline(
        tmp_path,
        prompt=prompt,
        image_enabled=False,
        image_theme="random_preset",
        image_theme_custom="可切回的旧主题",
    )
    pipeline.store.save_run(
        "测试群",
        "2026-08-17",
        {
            "status": "SENT",
            "prompt_meta": {
                "layout_id": "hero_cover",
                "comedy_device": "字面化",
                "layout_signature": "old-layout",
            },
        },
    )
    pipeline.store.save_run(
        "测试群",
        "2026-08-18",
        {
            "status": "PENDING",
            "prompt_meta": {
                "layout_id": "group_court",
                "hero_topic_id": "topic-01",
                "comedy_device": "反差",
            },
        },
    )
    result = pipeline.generate_all(run_date="2026-08-18")
    assert result[0]["status"] == "ready_to_send"
    assert prompt.inputs[0].image_theme == "random_preset"
    assert prompt.inputs[0].image_theme_custom == "可切回的旧主题"
    assert prompt.inputs[0].persisted_theme_meta["layout_id"] == "group_court"
    assert prompt.inputs[0].recent_layout_history[0]["layout_id"] == "hero_cover"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["image_theme"] == "random_preset"
    assert run["image_theme_custom"] == "可切回的旧主题"


def test_prompt_visible_group_name_prefers_name_saved_in_run(tmp_path):
    prompt = FakePrompt()
    pipeline, group = _make_pipeline(tmp_path, prompt=prompt, image_enabled=False)
    group.wechat_group_name = "数据库实时名 V5"
    pipeline.store.save_run(
        "测试群",
        "2026-08-18",
        {"status": "PENDING", "wechat_group_name": "运行已同步名 V4"},
    )
    window = pipeline.period_resolver.resolve(
        run_date=date(2026, 8, 18),
        timezone=pipeline.settings.app_timezone,
    )

    result = pipeline._generate_one(group, window, "2026-08-18", force=True)

    assert result["status"] == "ready_to_send"
    assert prompt.inputs[0].group_name == "测试群"
    assert prompt.inputs[0].visible_group_name == "运行已同步名 V4"


def test_generate_one_refreshes_current_image_switch_before_decision(tmp_path):
    pipeline, group = _make_pipeline(tmp_path, image_enabled=False)
    assert group.image_enabled is False

    # 保留传入的旧对象为 false，只把数据库中的当前配置切换为 true。
    with Session(repo.engine) as session:
        current = repo.get_group(session, group.id)
        assert current is not None
        current.image_enabled = True
        repo.save_group(session, current)

    window = pipeline.period_resolver.resolve(
        run_date=date(2026, 8, 18),
        timezone=pipeline.settings.app_timezone,
    )
    result = pipeline._generate_one(group, window, "2026-08-18", force=True)
    run = pipeline.store.load_run("测试群", "2026-08-18")

    assert result["need_image"] is True
    assert run["image_enabled"] is True


def test_force_generate_runs_image_queue_and_returns_final_state(tmp_path):
    gen = FakeGenerator()
    pipeline, group = _make_pipeline(tmp_path, gen=gen)
    result = pipeline.force_generate(group.id, "2026-08-18")
    assert result["status"] == "ready_to_send"
    assert len(gen.calls) == 1
    assert pipeline.store.load_run("测试群", "2026-08-18")["status"] == READY_TO_SEND


def test_force_generate_blocks_corrupt_state_before_name_sync(tmp_path, monkeypatch):
    pipeline, group = _make_pipeline(tmp_path)
    run_path = pipeline.store.run_path("测试群", "2026-08-18")
    run_path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"status": "PENDING"'
    run_path.write_bytes(original)
    monkeypatch.setattr(
        pipeline,
        "_sync_group_names",
        lambda *_args, **_kwargs: pytest.fail("损坏状态不得触发群名同步或生成"),
    )

    result = pipeline.force_generate(group.id, "2026-08-18")

    assert result == {
        "group_name": "测试群",
        "status": "blocked",
        "error_type": "RUN_STATE_CORRUPT",
        "detail": "运行状态文件损坏，需人工复核",
    }
    assert run_path.read_bytes() == original


def test_force_generate_image_failure_uses_local_fallback(tmp_path):
    gen = FakeGenerator(fail=True)
    pipeline, group = _make_pipeline(tmp_path, gen=gen)
    result = pipeline.force_generate(group.id, "2026-08-18")
    assert result["status"] == "ready_to_send"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["image_fallback_level"] == 3
    assert run["image_variant"] == "pillow"


def test_image_success_clears_stale_failure_fields(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    pipeline.store.update(
        "测试群",
        "2026-08-18",
        status=FAILED,
        failed_stage="image",
        error="旧生图失败",
        error_type=IMAGE_GENERATION_FAILED,
        image_error="旧生图失败",
    )
    job = pipeline._make_image_job(group, "2026-08-18", force=True)

    pipeline._image_hook(
        job,
        {
            "success": True,
            "status": "success",
            "detail": "图片已落盘",
            "error_type": "",
            "imagegen_ms": 1,
            "generator_detail": {},
        },
    )

    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == IMAGE_READY
    assert run["failed_stage"] is None
    assert run["error"] is None
    assert run["error_type"] is None
    assert run["image_error"] is None


def test_generate_data_failure_marks_failed(tmp_path):
    source = FakeSource(fail=True, error_type="WECHAT_DATA_UNAVAILABLE")
    pipeline, group = _make_pipeline(tmp_path, source=source)
    results = pipeline.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "WECHAT_DATA_UNAVAILABLE"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == FAILED
    assert run["failed_stage"] == "data"


def test_generate_prompt_failure_uses_local_infographic(tmp_path):
    preserved_meta = {
        "topic_selection": {"selected_topic_ids": ["topic-01"]},
        "layout_id": "split_focus",
    }
    pipeline, group = _make_pipeline(
        tmp_path,
        prompt=FakePrompt(fail=True, failure_meta=preserved_meta),
    )
    results = pipeline.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "ready_to_send"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["prompt_fallback_level"] == 3
    assert run["image_fallback_level"] == 3
    assert run["image_variant"] == "pillow"
    assert run["prompt_meta"]["topic_selection"] == preserved_meta["topic_selection"]
    assert run["prompt_meta"]["layout_id"] == "split_focus"
    assert run["prompt_meta"]["mode"] == "local_infographic"
    assert pipeline.store.image_path("测试群", "2026-08-18").is_file()


def test_image_disabled_goes_ready_to_send_without_image(tmp_path):
    pipeline, group = _make_pipeline(tmp_path, image_enabled=False)
    results = pipeline.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "ready_to_send"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert not pipeline.store.image_path("测试群", "2026-08-18").exists()


def test_image_serial_order(tmp_path):
    gen = FakeGenerator()
    pipeline, group = _make_pipeline(tmp_path, gen=gen)
    pipeline.generate_all(run_date="2026-08-18")
    assert len(gen.calls) == 1


def test_existing_valid_image_reconciles_failed_state_without_regeneration(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    pipeline.generate_all(run_date="2026-08-18")
    previous = pipeline.store.load_run("测试群", "2026-08-18")
    pipeline.store.update(
        "测试群",
        "2026-08-18",
        status=FAILED,
        failed_stage="image",
        error="模拟图片落盘后进程中断",
        image_job=previous["image_job"],
    )
    generator = FakeGenerator()
    pipeline.image_generator = generator

    job = pipeline._make_image_job(group, "2026-08-18", force=False)
    result = pipeline._run_image_jobs([job], "2026-08-18")

    assert result[0]["status"] == "ready_to_send"
    assert generator.calls == []
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["image_job"]["prompt_sha256"] == previous["image_job"]["prompt_sha256"]


def test_identical_generation_failure_exhausts_budget_after_real_retries(tmp_path, monkeypatch):
    source = FakeSource(fail=True)
    pipeline, _ = _make_pipeline(tmp_path, source=source, image_enabled=False)
    monkeypatch.setattr("app.pipeline.generation_stages.retry_is_due", lambda _run: True)

    first = pipeline.generate_all(run_date="2026-08-18")
    second = pipeline.generate_all(run_date="2026-08-18")
    third = pipeline.generate_all(run_date="2026-08-18")
    final = pipeline.generate_all(run_date="2026-08-18")

    assert [first[0]["status"], second[0]["status"], third[0]["status"]] == [
        "failed",
        "failed",
        "failed",
    ]
    assert final[0]["status"] == "failed_final"
    assert source.fetch_calls == 3
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["retry_attempt_count"] == 3
    assert run["execution_state"] == "FAILED_FINAL"
    assert len(run["attempt_ledger"]) == 3


def test_weekday_default_skips_saturday(tmp_path):
    pipeline, group = _make_pipeline(tmp_path, schedule_rule="weekday_default")
    results = pipeline.generate_all(run_date="2026-08-22")  # 周六
    assert results == [{"status": "no_groups", "reason": "当日没有符合群级统计规则的任务"}]
    run = pipeline.store.load_run("测试群", "2026-08-22")
    assert run["status"] == "PENDING"


# ---------- 发送阶段 ----------


def _ready_to_send(tmp_path, image_enabled=True, send_time="08:30") -> DailyPipeline:
    pipeline, _ = _make_pipeline(tmp_path, image_enabled=image_enabled, send_time=send_time)
    pipeline.generate_all(run_date="2026-08-18")
    return pipeline


def test_send_due_sends_text_then_image(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    now = datetime(2026, 8, 18, 9, 0, 0)  # 超过 08:30
    results = pipeline.send_due(now=now)
    assert results[0]["status"] == "sent"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == SENT
    assert run.get("sent_at")
    assert run["send_state"] == "sent"
    assert run["text_attempt_started_at"]
    assert run["text_attempt_finished_at"]
    assert run["image_attempt_started_at"]
    assert run["image_attempt_finished_at"]
    assert run["verification_level"] == "provider_reported"
    evidence = run["delivery_evidence"]
    assert evidence["target"] == "测试群"
    assert evidence["result"] == "sent"
    assert evidence["verification_level"] == "provider_reported"
    assert len(evidence["text_sha256"]) == 64
    assert len(evidence["image_sha256"]) == 64
    sender = pipeline.sender
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 1


def test_explicit_text_failures_use_backoff_and_stop_after_send_retry_budget(tmp_path):
    sender = FakeSender(fail_text=True)
    pipeline, _ = _make_pipeline(tmp_path, sender=sender, image_enabled=False)
    pipeline.generate_all(run_date="2026-08-18")

    first = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    assert first[0]["status"] == "retry_scheduled"
    assert len(sender.text_calls) == 1
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_retry_attempt_count"] == 1
    assert run["send_next_retry_at"] == "2026-08-18T08:32:00"

    early = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 30))
    assert early[0]["status"] == "retry_scheduled"
    assert len(sender.text_calls) == 1

    second = pipeline.send_due(now=datetime(2026, 8, 18, 8, 32, 0))
    assert second[0]["status"] == "retry_scheduled"
    assert len(sender.text_calls) == 2
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_next_retry_at"] == "2026-08-18T08:37:00"

    third = pipeline.send_due(now=datetime(2026, 8, 18, 8, 38, 0))
    assert third[0]["status"] == "failed_final"
    assert len(sender.text_calls) == 3
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_state"] == "failed_final"
    assert run["send_hold"] is True
    assert run["send_hold_reason"] == "SEND_RETRY_EXHAUSTED"
    assert run["send_retry_attempt_count"] == 3
    assert len(run["send_failure_ledger"]) == 3

    assert pipeline.send_due(now=datetime(2026, 8, 18, 8, 39, 0)) == []
    assert len(sender.text_calls) == 3


def test_explicit_unsubmitted_final_failure_can_be_reset_without_sending(tmp_path):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    sender = pipeline.sender
    failed = pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_state="failed_final",
        send_hold=True,
        send_hold_reason="SEND_RETRY_EXHAUSTED",
        needs_manual_send=True,
        send_retry_attempt_count=3,
        send_next_retry_at="",
        send_last_failure_at="2026-08-18T08:38:00",
        send_error="目标匹配数 0",
        send_error_type="SEND_TEXT_FAILED",
        send_failure_ledger=[{"attempt": 3, "error_type": "SEND_TEXT_FAILED"}],
        text_attempt_started_at="2026-08-18T08:38:00",
        text_attempt_finished_at="2026-08-18T08:38:01",
        text_submitted_at="",
        text_verified_at="",
        text_sent_at="",
        image_submitted_at="",
        image_verified_at="",
        image_sent_at="",
    )
    calls_before = (len(sender.text_calls), len(sender.image_calls))

    reset = pipeline.reset_explicit_send_failure(
        1,
        "2026-08-18",
        expected_updated_at=failed["updated_at"],
        expected_state_version=failed["state_version"],
    )
    stale = pipeline.reset_explicit_send_failure(
        1,
        "2026-08-18",
        expected_updated_at=failed["updated_at"],
        expected_state_version=failed["state_version"],
    )
    run = pipeline.store.load_run("测试群", "2026-08-18")

    assert reset["status"] == "prepared"
    assert stale["status"] == "conflict"
    assert stale["reason"] == "stale"
    assert run["send_state"] == "ready"
    assert run["send_hold"] is False
    assert run["send_retry_attempt_count"] == 0
    assert run["send_failure_ledger"] == failed["send_failure_ledger"]
    assert run["send_retry_reset_history"][-1]["previous_attempt_count"] == 3
    assert (len(sender.text_calls), len(sender.image_calls)) == calls_before


@pytest.mark.parametrize(
    "evidence_field",
    [
        "sent_at",
        "send_unknown_at",
        "text_submitted_at",
        "text_verified_at",
        "text_sent_at",
        "image_submitted_at",
        "image_verified_at",
        "image_sent_at",
    ],
)
def test_final_failure_reset_rejects_any_submission_or_delivery_evidence(
    tmp_path, evidence_field
):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    failed = pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_state="failed_final",
        send_hold=True,
        send_hold_reason="SEND_RETRY_EXHAUSTED",
        send_retry_attempt_count=3,
        **{evidence_field: "2026-08-18T08:38:00"},
    )

    result = pipeline.reset_explicit_send_failure(
        1,
        "2026-08-18",
        expected_updated_at=failed["updated_at"],
        expected_state_version=failed["state_version"],
    )

    assert result["status"] == "conflict"
    assert result["reason"] == "submission_evidence"


def test_final_failure_reset_rejects_unknown_state(tmp_path):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    unknown = pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_state="unknown",
        send_hold=True,
        send_hold_reason="SEND_RESULT_UNKNOWN",
        send_unknown_at="2026-08-18T08:38:00",
    )

    result = pipeline.reset_explicit_send_failure(
        1,
        "2026-08-18",
        expected_updated_at=unknown["updated_at"],
        expected_state_version=unknown["state_version"],
    )

    assert result["status"] == "conflict"
    assert result["reason"] == "not_explicit_failure"


def test_final_failure_reset_rejects_non_exhausted_retry_budget(tmp_path):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    inconsistent = pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_state="failed_final",
        send_hold=True,
        send_hold_reason="SEND_RETRY_EXHAUSTED",
        send_retry_attempt_count=1,
        send_retry_budget=3,
    )

    result = pipeline.reset_explicit_send_failure(
        1,
        "2026-08-18",
        expected_updated_at=inconsistent["updated_at"],
        expected_state_version=inconsistent["state_version"],
    )

    assert result["status"] == "conflict"
    assert result["reason"] == "not_explicit_failure"


def test_image_retry_preserves_confirmed_text_checkpoint(tmp_path):
    sender = FakeSender(fail_image=True)
    pipeline, _ = _make_pipeline(tmp_path, sender=sender, image_enabled=True)
    pipeline.generate_all(run_date="2026-08-18")

    first = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    assert first[0]["status"] == "retry_scheduled"
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 1
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["text_sent_at"]
    assert not run.get("image_sent_at")

    sender.fail_image = False
    second = pipeline.send_due(now=datetime(2026, 8, 18, 8, 32, 0))
    assert second[0]["status"] == "sent"
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 2
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == SENT
    assert run["send_next_retry_at"] == ""


def test_send_due_refreshes_renamed_group_by_stable_id_without_changing_archive_name(tmp_path):
    source = FakeSource()
    pipeline, group = _make_pipeline(tmp_path, source=source)
    pipeline.generate_all(run_date="2026-08-18")
    source.group_name = "测试群新名称"

    results = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))

    assert results[0]["status"] == "sent"
    assert [target for target, _ in pipeline.sender.text_calls] == ["测试群新名称"]
    assert [target for target, _ in pipeline.sender.image_calls] == ["测试群新名称"]
    with Session(repo.engine) as session:
        saved = session.get(Group, group.id)
        assert saved.display_name == "测试群"
        assert saved.wechat_group_name == "测试群新名称"
        assert saved.send_target == ""
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["name_sync_status"] == "fresh"
    assert run["effective_send_target"] == "测试群新名称"
    assert run["sent_target"] == "测试群新名称"


def test_send_due_preserves_manual_target_after_current_name_changes(tmp_path):
    source = FakeSource()
    pipeline, group = _make_pipeline(tmp_path, source=source, image_enabled=False)
    pipeline.generate_all(run_date="2026-08-18")
    with Session(repo.engine) as session:
        saved = session.get(Group, group.id)
        saved.send_target = "人工搜索目标"
        repo.save_group(session, saved)
    source.group_name = "微信新名称"

    result = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))[0]

    assert result["status"] == "sent"
    assert [target for target, _ in pipeline.sender.text_calls] == ["人工搜索目标"]
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["name_sync_status"] == "manual_override"
    assert run["effective_send_target"] == "人工搜索目标"
    with Session(repo.engine) as session:
        saved = session.get(Group, group.id)
        assert saved.wechat_group_name == "微信新名称"
        assert saved.send_target == "人工搜索目标"


def test_send_due_uses_cached_name_when_live_sync_is_unavailable(tmp_path):
    source = FakeSource()
    pipeline, _ = _make_pipeline(tmp_path, source=source, image_enabled=False)
    pipeline.generate_all(run_date="2026-08-18")
    source.available = False

    result = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))[0]

    assert result["status"] == "sent"
    assert [target for target, _ in pipeline.sender.text_calls] == ["测试群"]
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["name_sync_status"] == "cached"
    assert run["effective_send_target"] == "测试群"


def test_send_due_processes_same_time_groups_in_stable_order(tmp_path, monkeypatch):
    sender = FakeSender()
    pipeline, _ = _make_pipeline(tmp_path, sender=sender, image_enabled=False)
    groups = [
        Group(
            id=101,
            display_name="顺序群一",
            wechat_group_name="顺序群一",
            send_target="目标一",
            send_time="08:30",
            image_enabled=False,
            wechat_send_enabled=True,
        ),
        Group(
            id=102,
            display_name="顺序群二",
            wechat_group_name="顺序群二",
            send_target="目标二",
            send_time="08:30",
            image_enabled=False,
            wechat_send_enabled=True,
        ),
    ]
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)
    for group in groups:
        pipeline.store.save_run(group.display_name, "2026-08-18", {"status": READY_TO_SEND})
        ranking_path = pipeline.store.ranking_txt_path(group.display_name, "2026-08-18")
        ranking_path.parent.mkdir(parents=True, exist_ok=True)
        ranking_path.write_text(f"{group.display_name}总结", encoding="utf-8")

    results = pipeline.send_due(now=datetime(2026, 8, 18, 8, 30, 0))

    assert [result["group_name"] for result in results] == ["顺序群一", "顺序群二"]
    assert [target for target, _ in sender.text_calls] == ["目标一", "目标二"]


def test_send_due_isolates_one_group_pre_submit_exception(tmp_path, monkeypatch):
    sender = FakeSender()
    pipeline, _ = _make_pipeline(tmp_path, sender=sender, image_enabled=False)
    groups = [
        Group(id=111, display_name="异常群", wechat_group_name="异常群", send_target="目标一", send_time="08:30", image_enabled=False, wechat_send_enabled=True),
        Group(id=112, display_name="正常群", wechat_group_name="正常群", send_target="目标二", send_time="08:30", image_enabled=False, wechat_send_enabled=True),
    ]
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)
    for group in groups:
        pipeline.store.save_run(group.display_name, "2026-08-18", {"status": READY_TO_SEND})
        path = pipeline.store.ranking_txt_path(group.display_name, "2026-08-18")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("日报", encoding="utf-8")
    original = pipeline._send_one

    def isolated_send(group, *args, **kwargs):
        if group.display_name == "异常群":
            raise RuntimeError("pre-submit")
        return original(group, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_send_one", isolated_send)
    results = pipeline.send_due(now=datetime(2026, 8, 18, 8, 30, 0))

    assert [item["status"] for item in results] == ["failed", "sent"]
    assert [target for target, _ in sender.text_calls] == ["目标二"]


def test_send_due_aborts_batch_when_desktop_submission_state_is_unknown(tmp_path, monkeypatch):
    sender = FakeSender()
    pipeline, _ = _make_pipeline(tmp_path, sender=sender, image_enabled=False)
    groups = [
        Group(id=121, display_name="未知群", wechat_group_name="未知群", send_target="目标一", send_time="08:30", image_enabled=False, wechat_send_enabled=True),
        Group(id=122, display_name="不应继续群", wechat_group_name="不应继续群", send_target="目标二", send_time="08:30", image_enabled=False, wechat_send_enabled=True),
    ]
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)
    for group in groups:
        pipeline.store.save_run(group.display_name, "2026-08-18", {"status": READY_TO_SEND})
        path = pipeline.store.ranking_txt_path(group.display_name, "2026-08-18")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("日报", encoding="utf-8")

    def unresolved_send(group, group_name, run, run_date, now, **kwargs):
        if group.display_name != "未知群":
            return pytest.fail("桌面发送状态未知后不得继续其他群")
        claim_id, _, _ = pipeline.store.claim_send(
            group_name,
            run_date,
            now=now,
            lease_seconds=60,
        )
        pipeline.store.update_send_claim(
            group_name,
            run_date,
            claim_id,
            send_state="sending_text",
            text_attempt_started_at=now.isoformat(),
            text_attempt_finished_at="",
        )
        raise RuntimeError("desktop crashed after submit")

    monkeypatch.setattr(pipeline, "_send_one", unresolved_send)
    results = pipeline.send_due(now=datetime(2026, 8, 18, 8, 30, 0))

    assert len(results) == 1
    assert results[0]["status"] == "held"
    assert results[0]["error_type"] == "SEND_RESULT_UNKNOWN"
    assert pipeline.store.load_run("未知群", "2026-08-18")["send_state"] == "unknown"
    assert pipeline.store.load_run("不应继续群", "2026-08-18")["status"] == READY_TO_SEND


def test_send_due_uses_cached_groups_when_sync_itself_raises(tmp_path, monkeypatch):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    monkeypatch.setattr(
        pipeline,
        "_sync_group_names",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("mcp unavailable")),
    )

    result = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))

    assert result[0]["status"] == "sent"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["name_sync_status"] == "cached"


def test_send_due_skips_group_when_wechat_send_is_not_enabled(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    from sqlmodel import select

    with Session(repo.engine) as session:
        group = session.exec(select(Group).where(Group.display_name == "测试群")).first()
        assert group is not None
        group.wechat_send_enabled = False
        repo.save_group(session, group)

    assert pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0)) == []
    assert pipeline.store.load_run("测试群", "2026-08-18")["status"] == READY_TO_SEND


def test_send_not_due_yet(tmp_path):
    pipeline = _ready_to_send(tmp_path, send_time="12:00")
    now = datetime(2026, 8, 18, 9, 0, 0)
    results = pipeline.send_due(now=now)
    assert results == []


def test_send_no_duplicate_after_sent(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    now = datetime(2026, 8, 18, 9, 0, 0)
    pipeline.send_due(now=now)
    results = pipeline.send_due(now=datetime(2026, 8, 18, 10, 0, 0))
    assert results == []  # 已 SENT 不再发送


def test_send_text_failure(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = FakeSender(fail_text=True)
    now = datetime(2026, 8, 18, 9, 0, 0)
    results = pipeline.send_due(now=now)
    assert results[0]["status"] == "retry_scheduled"
    assert results[0]["error_type"] == "SEND_TEXT_FAILED"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["send_error_type"] == "SEND_TEXT_FAILED"


def test_send_preflight_missing_ranking_does_not_call_sender(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    ranking = pipeline.store.ranking_txt_path("测试群", "2026-08-18")
    ranking.unlink()
    sender = FakeSender()
    pipeline.sender = sender
    results = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "SEND_TEXT_FAILED"
    assert sender.text_calls == []
    assert sender.image_calls == []


def test_send_preflight_empty_ranking_does_not_call_sender(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.store.ranking_txt_path("测试群", "2026-08-18").write_text("", encoding="utf-8")
    sender = FakeSender()
    pipeline.sender = sender
    results = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "SEND_TEXT_FAILED"
    assert sender.text_calls == []
    assert sender.image_calls == []


def test_send_preflight_missing_image_does_not_call_sender(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.store.image_path("测试群", "2026-08-18").unlink()
    sender = FakeSender()
    pipeline.sender = sender
    results = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == IMAGE_FILE_MISSING
    assert sender.text_calls == []
    assert sender.image_calls == []


def test_send_image_disabled_sends_text_only(tmp_path):
    pipeline = _ready_to_send(tmp_path, image_enabled=False)
    sender = FakeSender()
    pipeline.sender = sender
    results = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))
    assert results[0]["status"] == "sent"
    assert "未启用图片" in results[0]["detail"]
    assert len(sender.text_calls) == 1
    assert sender.image_calls == []


def test_send_image_failure(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = FakeSender(fail_image=True)
    now = datetime(2026, 8, 18, 9, 0, 0)
    results = pipeline.send_due(now=now)
    assert results[0]["status"] == "retry_scheduled"
    assert results[0]["error_type"] == "SEND_IMAGE_FAILED"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    assert run["text_sent_at"]


def test_send_image_retry_does_not_repeat_text(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    first_sender = FakeSender(fail_image=True)
    pipeline.sender = first_sender
    pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))
    assert len(first_sender.text_calls) == 1

    retry_sender = FakeSender()
    pipeline.sender = retry_sender
    result = pipeline.send_due_for_dates(
        ["2026-08-18"],
        now=datetime(2026, 8, 18, 9, 1, 0),
        recovery=True,
    )

    assert result[0]["status"] == "sent"
    assert retry_sender.text_calls == []
    assert len(retry_sender.image_calls) == 1


def test_force_send(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    r = pipeline.force_send(1, "2026-08-18", confirm_late_send=True)
    assert r["status"] == "sent"


def test_force_send_cannot_bypass_prompt_saved_hold(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_hold=True,
        image_regen_status="prompt_saved",
    )

    result = pipeline.force_send(
        1, "2026-08-18", confirm_regenerated=True, confirm_late_send=True
    )

    assert result["status"] == "failed"
    assert "新图尚未完成审核" in result["error"]
    assert pipeline.sender.text_calls == []


def test_force_send_regenerated_image_requires_explicit_confirmation(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.store.update(
        "测试群",
        "2026-08-18",
        send_hold=True,
        image_regen_status="ready_for_review",
    )

    rejected = pipeline.force_send(1, "2026-08-18")
    accepted = pipeline.force_send(
        1, "2026-08-18", confirm_regenerated=True, confirm_late_send=True
    )

    assert rejected["status"] == "failed"
    assert accepted["status"] == "sent"


def test_send_due_allows_exact_30_minute_boundary(tmp_path):
    pipeline = _ready_to_send(tmp_path)

    result = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))

    assert result[0]["status"] == "sent"


def test_send_due_holds_after_30_minute_boundary(tmp_path):
    pipeline = _ready_to_send(tmp_path)

    result = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 1))

    assert result[0]["status"] == "held"
    assert result[0]["error_type"] == "MISSED_SEND_WINDOW"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_hold"] is True
    assert run["send_hold_reason"] == "MISSED_SEND_WINDOW"
    assert pipeline.sender.text_calls == []


def test_force_send_late_requires_independent_confirmation(tmp_path):
    pipeline = _ready_to_send(tmp_path)

    rejected = pipeline.force_send(1, "2026-08-18")
    accepted = pipeline.force_send(1, "2026-08-18", confirm_late_send=True)

    assert rejected["error_type"] == "MISSED_SEND_WINDOW"
    assert accepted["status"] == "sent"


def test_submitted_but_unverified_text_is_held_without_retry(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    sender = UnknownTextSender()
    pipeline.sender = sender

    first = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    second = pipeline.send_due(now=datetime(2026, 8, 18, 8, 32, 0))

    assert first[0]["error_type"] == "SEND_RESULT_UNKNOWN"
    assert second == []
    assert len(sender.text_calls) == 1
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_state"] == "unknown"
    assert run["send_hold"] is True


def test_submitted_failure_is_still_held_as_unknown(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    sender = SubmittedFailureTextSender()
    pipeline.sender = sender

    result = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))

    assert result[0]["status"] == "held"
    assert result[0]["error_type"] == "SEND_RESULT_UNKNOWN"
    assert len(sender.text_calls) == 1
    assert sender.image_calls == []
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_state"] == "unknown"


def test_text_success_persistence_failure_holds_before_image(tmp_path, monkeypatch):
    pipeline = _ready_to_send(tmp_path)
    original = pipeline.store.update_send_claim
    calls = 0

    def flaky_update(*args, **kwargs):
        nonlocal calls
        calls += 1
        # 第 1 次先原子保存送达证据，第 2 次标记文字提交中，
        # 第 3 次模拟文字已成功但成功检查点无法落盘。
        if calls == 3:
            return False, pipeline.store.load_run("测试群", "2026-08-18")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.store, "update_send_claim", flaky_update)
    result = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))

    assert result[0]["status"] == "held"
    assert result[0]["error_type"] == "SEND_RESULT_UNKNOWN"
    assert len(pipeline.sender.text_calls) == 1
    assert pipeline.sender.image_calls == []
    assert pipeline.store.load_run("测试群", "2026-08-18")["send_state"] == "unknown"


def test_image_claim_update_failure_stops_before_external_submit(tmp_path, monkeypatch):
    pipeline = _ready_to_send(tmp_path)
    original = pipeline.store.update_send_claim
    calls = 0

    def flaky_update(*args, **kwargs):
        nonlocal calls
        calls += 1
        # 保存证据、文字提交中、文字成功检查点之后，
        # 第 4 次在图片外部提交前模拟 claim 丢失。
        if calls == 4:
            return False, pipeline.store.load_run("测试群", "2026-08-18")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.store, "update_send_claim", flaky_update)
    result = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))

    assert result[0]["status"] == "skipped"
    assert result[0]["error_type"] == "SEND_CLAIM_LOST"
    assert len(pipeline.sender.text_calls) == 1
    assert pipeline.sender.image_calls == []


def test_sent_terminal_persistence_failure_becomes_unknown_hold(tmp_path, monkeypatch):
    pipeline = _ready_to_send(tmp_path)
    original = pipeline.store.finish_send_claim

    def flaky_finish(*args, **kwargs):
        if kwargs.get("send_state") == "sent":
            return False, pipeline.store.load_run("测试群", "2026-08-18")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.store, "finish_send_claim", flaky_finish)
    result = pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))

    assert result[0]["status"] == "held"
    assert result[0]["error_type"] == "SEND_RESULT_UNKNOWN"
    assert len(pipeline.sender.text_calls) == 1
    assert len(pipeline.sender.image_calls) == 1
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["send_state"] == "unknown"
    assert run["status"] == READY_TO_SEND


def test_manual_text_sent_resolution_continues_image_without_resending_text(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = UnknownTextSender()
    pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    unknown = pipeline.store.load_run("测试群", "2026-08-18")
    sender = FakeSender()
    pipeline.sender = sender

    resolved = pipeline.resolve_send_unknown(
        1,
        "2026-08-18",
        resolution="text_sent",
        expected_send_unknown_at=unknown["send_unknown_at"],
    )

    assert resolved["status"] == "resolved"
    assert resolved["next_stage"] == "image"
    assert sender.text_calls == []
    assert sender.image_calls == []
    sent = pipeline.force_send(1, "2026-08-18", confirm_late_send=True)
    assert sent["status"] == "sent"
    assert sender.text_calls == []
    assert len(sender.image_calls) == 1


def test_manual_not_sent_resolution_allows_one_fresh_full_send(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = UnknownTextSender()
    pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    unknown = pipeline.store.load_run("测试群", "2026-08-18")
    sender = FakeSender()
    pipeline.sender = sender

    resolved = pipeline.resolve_send_unknown(
        1,
        "2026-08-18",
        resolution="not_sent",
        expected_send_unknown_at=unknown["send_unknown_at"],
    )
    stale = pipeline.resolve_send_unknown(
        1,
        "2026-08-18",
        resolution="not_sent",
        expected_send_unknown_at=unknown["send_unknown_at"],
    )

    assert resolved["status"] == "resolved"
    assert stale["status"] == "conflict"
    sent = pipeline.force_send(1, "2026-08-18", confirm_late_send=True)
    assert sent["status"] == "sent"
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 1


def test_manual_all_sent_resolution_is_cas_protected_and_never_calls_sender(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    unknown_sender = UnknownTextSender()
    pipeline.sender = unknown_sender
    pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    held = pipeline.store.load_run("测试群", "2026-08-18")

    resolved = pipeline.resolve_manual_send(
        1,
        "2026-08-18",
        resolution="all_sent",
        expected_updated_at=held["updated_at"],
    )
    stale = pipeline.resolve_manual_send(
        1,
        "2026-08-18",
        resolution="all_sent",
        expected_updated_at=held["updated_at"],
    )
    run = pipeline.store.load_run("测试群", "2026-08-18")

    assert resolved["status"] == "resolved"
    assert resolved["next_stage"] == "complete"
    assert stale["status"] == "conflict"
    assert run["status"] == SENT
    assert run["send_state"] == "sent"
    assert run["send_hold"] is False
    assert run["verification_level"] == "manual_user_confirmed"
    assert run["send_resolution_history"][-1]["resolution"] == "all_sent"
    assert len(unknown_sender.text_calls) == 1
    assert unknown_sender.image_calls == []


def test_manual_resolution_rejects_missing_image_without_mutating_hold(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = UnknownTextSender()
    pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    held = pipeline.store.load_run("测试群", "2026-08-18")
    pipeline.store.image_path("测试群", "2026-08-18").unlink()

    rejected = pipeline.resolve_manual_send(
        1,
        "2026-08-18",
        resolution="all_sent",
        expected_updated_at=held["updated_at"],
    )
    run = pipeline.store.load_run("测试群", "2026-08-18")

    assert rejected["status"] == "conflict"
    assert rejected["reason"] == "image_missing"
    assert run["send_hold"] is True


@pytest.mark.parametrize(
    ("resolution", "expected_text_sent", "expected_next_stage"),
    [
        ("text_sent", True, "image"),
        ("not_sent", False, "text"),
    ],
)
def test_manual_partial_resolutions_update_only_run_state(
    tmp_path, resolution, expected_text_sent, expected_next_stage
):
    pipeline = _ready_to_send(tmp_path)
    sender = UnknownTextSender()
    pipeline.sender = sender
    pipeline.send_due(now=datetime(2026, 8, 18, 8, 31, 0))
    held = pipeline.store.load_run("测试群", "2026-08-18")
    calls_before = (len(sender.text_calls), len(sender.image_calls))

    resolved = pipeline.resolve_manual_send(
        1,
        "2026-08-18",
        resolution=resolution,
        expected_updated_at=held["updated_at"],
    )
    run = pipeline.store.load_run("测试群", "2026-08-18")

    assert resolved["status"] == "resolved"
    assert resolved["next_stage"] == expected_next_stage
    assert run["status"] == READY_TO_SEND
    assert run["send_hold"] is False
    assert bool(run.get("text_sent_at")) is expected_text_sent
    assert run["send_resolution_history"][-1]["resolution"] == resolution
    assert (len(sender.text_calls), len(sender.image_calls)) == calls_before


def test_run_store_send_claim_prevents_duplicate_and_marks_expired_attempt_unknown(tmp_path):
    store = RunStore(tmp_path / "output")
    store.save_run("测试群", "2026-08-18", {"status": READY_TO_SEND})
    now = datetime(2026, 8, 18, 8, 30, 0)

    claim_id, _, reason = store.claim_send(
        "测试群", "2026-08-18", now=now, lease_seconds=30
    )
    duplicate, _, duplicate_reason = store.claim_send(
        "测试群", "2026-08-18", now=now, lease_seconds=30
    )

    assert claim_id and reason == "claimed"
    assert duplicate is None and duplicate_reason == "already_claimed"
    store.update_send_claim(
        "测试群",
        "2026-08-18",
        claim_id,
        text_attempt_started_at=now.isoformat(),
        text_attempt_finished_at="",
    )
    recovered, run, recovered_reason = store.claim_send(
        "测试群", "2026-08-18", now=now + timedelta(seconds=31), lease_seconds=30
    )
    assert recovered is None
    assert recovered_reason == "result_unknown"
    assert run["send_state"] == "unknown"
