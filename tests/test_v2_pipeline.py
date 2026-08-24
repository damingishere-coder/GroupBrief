"""V2 P7：DailyPipeline 集成测试。

注入 Fake 数据源/DeepSeek/生图/发送，隔离所有外部依赖，验证：
状态机推进 / 文件生成 / 防重复 / force / 失败隔离 / 生图串行 /
发送到点 / 不重复发送 / 周六跳过。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

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
    def __init__(self, fail=False):
        self.fail = fail
        self.inputs = []

    def build(self, data):
        from app.ai.prompt_builder_types import PromptOutput

        self.inputs.append(data)
        if self.fail:
            return PromptOutput(False, error="DeepSeek 失败")
        return PromptOutput(True, "【任务】\n生成图片\n【主标题】今天热聊", meta={"mode": "single"})


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
        output_path.write_bytes(
            bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806"
                          "0000001f15c4890000000d4944415478da63f8cfc0f80100050001fff83f240000000049454e44ae426082")
        )
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


def _make_pipeline(tmp_path, source=None, prompt=None, gen=None, sender=None, image_enabled=True, send_time="08:30", image_theme="blue_white", image_theme_custom=""):
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
    assert generator.calls == []
    assert snapshot_path.read_bytes() == snapshot_before
    assert image_path.read_bytes() == image_before
    run = pipeline2.store.load_run("测试群", "2026-08-18")
    assert run["status"] == PROMPT_READY
    assert run["message_snapshot_reused"] is True
    assert run["prompt_rebuild_status"] == "ready_for_review"
    assert run["image_regen_status"] == "prompt_rebuilt"
    assert run["send_hold"] is True


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


def test_force_generate_image_failure_returns_failed_state(tmp_path):
    gen = FakeGenerator(fail=True)
    pipeline, group = _make_pipeline(tmp_path, gen=gen)
    result = pipeline.force_generate(group.id, "2026-08-18")
    assert result["status"] == "failed"
    assert result["error_type"] == IMAGE_GENERATION_FAILED
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == FAILED


def test_generate_data_failure_marks_failed(tmp_path):
    source = FakeSource(fail=True, error_type="WECHAT_DATA_UNAVAILABLE")
    pipeline, group = _make_pipeline(tmp_path, source=source)
    results = pipeline.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "WECHAT_DATA_UNAVAILABLE"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == FAILED
    assert run["failed_stage"] == "data"


def test_generate_prompt_failure_marks_failed(tmp_path):
    pipeline, group = _make_pipeline(tmp_path, prompt=FakePrompt(fail=True))
    results = pipeline.generate_all(run_date="2026-08-18")
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "PROMPT_FAILED"
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == FAILED


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


def test_saturday_generates_previous_day(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    results = pipeline.generate_all(run_date="2026-08-22")  # 周六
    assert results[0]["status"] == "ready_to_send"
    run = pipeline.store.load_run("测试群", "2026-08-22")
    assert run["period_start"] == "2026-08-21 00:00:00"
    assert run["period_end"] == "2026-08-21 23:59:59"


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
    sender = pipeline.sender
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 1


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
    assert results[0]["status"] == "failed"
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
    assert results[0]["status"] == "failed"
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
    result = pipeline.send_due(now=datetime(2026, 8, 18, 9, 0, 0))

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
