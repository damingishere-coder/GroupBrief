"""V2 P7：DailyPipeline 集成测试。

注入 Fake 数据源/DeepSeek/生图/发送，隔离所有外部依赖，验证：
状态机推进 / 文件生成 / 防重复 / force / 失败隔离 / 生图串行 /
发送到点 / 不重复发送 / 周六跳过。
"""

from __future__ import annotations

from datetime import date, datetime, time
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
from app.db.models import Group
from app.pipeline.daily_pipeline import DailyPipeline
from app.v2.constants import (
    FAILED,
    IMAGE_READY,
    PROMPT_READY,
    READY_TO_SEND,
    SENT,
)
from app.v2.run_store import RunStore


def _clear_groups() -> None:
    with Session(repo.engine) as session:
        for g in repo.list_groups(session):
            repo.delete_group(session, g.id)


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

    def __init__(self, messages=None, fail=False, error_type=""):
        self.messages = messages or [_msg("张三", "今天聊了票房", i=1), _msg("李四", "《牛来》破500万", i=2)]
        self.fail = fail
        self.error_type = error_type

    def health_check(self) -> DataSourceHealth:
        return DataSourceHealth(DataSourceStatus.OK, "ok")

    def list_groups(self) -> list[ResolvedGroup]:
        return [ResolvedGroup(group_id="g@chatroom", group_name="测试群")]

    def resolve_group(self, name: str) -> list[ResolvedGroup]:
        return []

    def fetch_messages(self, group_id, start_time, end_time) -> FetchResult:
        if self.fail:
            return FetchResult([], DataSourceStatus.READ_FAILED, "取数失败", self.error_type or "MESSAGE_FETCH_FAILED")
        return FetchResult(self.messages, DataSourceStatus.OK, "ok")


class FakePrompt:
    def __init__(self, fail=False):
        self.fail = fail

    def build(self, data):
        from app.ai.prompt_builder_types import PromptOutput

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


def _make_pipeline(tmp_path, source=None, prompt=None, gen=None, sender=None, image_enabled=True, send_time="08:30"):
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
            )
        else:
            group.image_enabled = image_enabled
            group.send_time = send_time
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
    assert results[0]["status"] in ("ready_to_send", "prompt_ready")
    run = pipeline.store.load_run("测试群", "2026-08-18")
    assert run["status"] == READY_TO_SEND
    # 文件生成
    assert pipeline.store.messages_path("测试群", "2026-08-18").exists()
    assert pipeline.store.ranking_json_path("测试群", "2026-08-18").exists()
    assert pipeline.store.ranking_txt_path("测试群", "2026-08-18").exists()
    assert pipeline.store.prompt_path("测试群", "2026-08-18").exists()
    assert pipeline.store.image_path("测试群", "2026-08-18").exists()


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
    assert results[0]["status"] in ("ready_to_send", "prompt_ready")


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


def test_saturday_skipped(tmp_path):
    pipeline, group = _make_pipeline(tmp_path)
    results = pipeline.generate_all(run_date="2026-08-22")  # 周六
    assert results[0]["status"] == "skipped"


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
    sender = pipeline.sender
    assert len(sender.text_calls) == 1
    assert len(sender.image_calls) == 1


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
    assert run["status"] == FAILED


def test_send_image_failure(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    pipeline.sender = FakeSender(fail_image=True)
    now = datetime(2026, 8, 18, 9, 0, 0)
    results = pipeline.send_due(now=now)
    assert results[0]["status"] == "failed"
    assert results[0]["error_type"] == "SEND_IMAGE_FAILED"


def test_force_send(tmp_path):
    pipeline = _ready_to_send(tmp_path)
    r = pipeline.force_send(1, "2026-08-18")
    assert r["status"] == "sent"
