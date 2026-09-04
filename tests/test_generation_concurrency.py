"""群级并发、全局槽位、稳定顺序和共享生成锁。"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

from app.ai.concurrency import bounded_slot
from app.ai.prompt_builder_types import PromptOutput
from app.config.settings import Settings
from app.data_sources.base import DataSourceStatus, FetchResult, V2Message, WeChatDataSource
from app.db.models import Group
from app.db.models import GroupRun
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline
from app.services.report_service import ReportService
from app.services.generation_runtime import GenerationBusyError, generation_mutex
from app.v2.run_store import RunStore


@pytest.fixture(autouse=True)
def _isolate_concurrency_tests_from_group_name_sync(monkeypatch):
    """并发测试不依赖数据库初始化，也不验证群名同步。"""

    monkeypatch.setattr(DailyPipeline, "_sync_group_names", lambda self, group_ids=None: None)


class DelayedSource(WeChatDataSource):
    name = "delayed"

    def __init__(self, barrier_participants: int = 0):
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.barrier = (
            threading.Barrier(barrier_participants)
            if barrier_participants > 1
            else None
        )
        self.barrier_slots = barrier_participants
        self.barrier_guard = threading.Lock()

    def fetch_messages(self, group_id, start_time, end_time):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        with self.barrier_guard:
            use_barrier = self.barrier is not None and self.barrier_slots > 0
            if use_barrier:
                self.barrier_slots -= 1
        if use_barrier:
            self.barrier.wait(timeout=5)
        else:
            time.sleep(0.06)
        with self.lock:
            self.active -= 1
        if group_id == "group-3":
            return FetchResult([], DataSourceStatus.READ_FAILED, "模拟单群失败", "MESSAGE_FETCH_FAILED")
        return FetchResult([
            V2Message(
                message_id=f"message-{group_id}",
                group_id=group_id,
                group_name=group_id,
                sender_id="sender",
                sender_name="成员",
                timestamp=datetime(2026, 8, 21, 10, 0),
                content="真实聊天内容",
            )
        ])


class DelayedPrompt:
    def __init__(self, settings: Settings, barrier_participants: int = 0):
        self.settings = settings
        self.lock = threading.Lock()
        self.active = 0
        self.maximum = 0
        self.barrier = (
            threading.Barrier(barrier_participants)
            if barrier_participants > 1
            else None
        )
        self.barrier_slots = barrier_participants
        self.barrier_guard = threading.Lock()

    def build(self, data):
        from app.ai.speaker_attribution import build_attribution_contract

        with bounded_slot("deepseek_request", self.settings.ai_request_concurrency):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            with self.barrier_guard:
                use_barrier = self.barrier is not None and self.barrier_slots > 0
                if use_barrier:
                    self.barrier_slots -= 1
            if use_barrier:
                self.barrier.wait(timeout=5)
            else:
                time.sleep(0.06)
            with self.lock:
                self.active -= 1
        contract = build_attribution_contract(data.messages)
        snapshot_hash = data.message_snapshot_sha256 or contract.message_snapshot_sha256
        speaker_fingerprint = data.speaker_fingerprint or contract.speaker_fingerprint
        return PromptOutput(
            True,
            "完整 Prompt",
            meta={
                "api_call_count": 1,
                "chunk_count": 1,
                "message_snapshot_sha256": snapshot_hash,
                "speaker_fingerprint": speaker_fingerprint,
            },
        )


class PromptReadyOrder:
    """让“慢群”必须观察到“快群”已开始生图后才能结束 Prompt。"""

    def __init__(self):
        self.fast_prompt_ready = threading.Event()
        self.image_started = threading.Event()
        self.slow_saw_image_start = False

    def build(self, data):
        from app.ai.speaker_attribution import build_attribution_contract

        contract = build_attribution_contract(data.messages)
        meta = {
            "api_call_count": 1,
            "chunk_count": 1,
            "message_snapshot_sha256": (
                data.message_snapshot_sha256 or contract.message_snapshot_sha256
            ),
            "speaker_fingerprint": data.speaker_fingerprint or contract.speaker_fingerprint,
        }
        if data.group_name == "快群":
            self.fast_prompt_ready.set()
            return PromptOutput(True, "Prompt 快群", meta=meta)

        assert self.fast_prompt_ready.wait(timeout=1)
        self.slow_saw_image_start = self.image_started.wait(timeout=2)
        return PromptOutput(True, "Prompt 慢群", meta=meta)


class ImmediateFakeImageGenerator:
    def __init__(self, prompt_order: PromptReadyOrder):
        self.prompt_order = prompt_order
        self.calls: list[str] = []
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(2)

    def generate(self, prompt_file, output_path):
        from app.image.image_task import ImageTaskResult

        prompt = prompt_file.read_text(encoding="utf-8")
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls.append(prompt)
        if prompt == "Prompt 快群":
            self.prompt_order.image_started.set()
        try:
            self.barrier.wait(timeout=5)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(output_path, format="PNG")
        finally:
            with self.lock:
                self.active -= 1
        return ImageTaskResult(True, image_path=output_path)


def test_five_groups_overlap_with_limits_order_and_failure_isolation(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        generation_group_concurrency=5,
        wechat_fetch_concurrency=3,
        ai_request_concurrency=2,
    )
    source = DelayedSource(barrier_participants=3)
    prompt = DelayedPrompt(settings, barrier_participants=2)
    groups = [
        Group(
            display_name=f"群{index}",
            wechat_group_id=f"group-{index}",
            image_enabled=False,
            image_theme="random_preset",
        )
        for index in range(5)
    ]
    pipeline = DailyPipeline(
        settings=settings,
        data_source=source,
        prompt_builder=prompt,
        store=RunStore(tmp_path / "output"),
        dry_run=True,
    )
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)

    results = pipeline.generate_all(run_date="2026-08-21")

    assert [item["group_name"] for item in results] == [f"群{index}" for index in range(5)]
    assert results[3]["status"] == "failed"
    assert all(item["status"] == "ready_to_send" for index, item in enumerate(results) if index != 3)
    assert 1 < source.maximum <= 3
    assert 1 < prompt.maximum <= 2


def test_prompt_ready_group_starts_image_before_other_prompts_finish(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        generation_group_concurrency=2,
        wechat_fetch_concurrency=2,
        ai_request_concurrency=2,
    )
    prompt_order = PromptReadyOrder()
    generator = ImmediateFakeImageGenerator(prompt_order)
    groups = [
        Group(
            display_name="慢群",
            wechat_group_id="slow-group",
            image_enabled=True,
            image_theme="random_preset",
        ),
        Group(
            display_name="快群",
            wechat_group_id="fast-group",
            image_enabled=True,
            image_theme="random_preset",
        ),
    ]
    pipeline = DailyPipeline(
        settings=settings,
        data_source=DelayedSource(),
        prompt_builder=prompt_order,
        image_generator=generator,
        store=RunStore(tmp_path / "output"),
        dry_run=True,
    )
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)

    results = pipeline.generate_all(run_date="2026-08-21")

    assert prompt_order.slow_saw_image_start is True
    assert generator.calls == ["Prompt 快群", "Prompt 慢群"]
    assert generator.maximum == 2
    assert [item["group_name"] for item in results] == ["慢群", "快群"]
    assert all(item["status"] == "ready_to_send" for item in results)


def test_group_overrides_rebuild_loaded_group_with_live_sqlalchemy_state(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'group-overrides.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)
    with Session(engine) as session:
        group = repo.save_group(
            session,
            Group(
                display_name="覆盖配置群",
                wechat_group_id="override-group",
                image_enabled=False,
                image_theme="random_preset",
            ),
        )
        group_id = int(group.id)

    settings = Settings(
        _env_file=None,
        generation_group_concurrency=1,
        wechat_fetch_concurrency=1,
        ai_request_concurrency=1,
    )
    pipeline = DailyPipeline(
        settings=settings,
        data_source=DelayedSource(),
        prompt_builder=DelayedPrompt(settings),
        store=RunStore(tmp_path / "output"),
        dry_run=True,
    )

    results = pipeline.generate_all(
        run_date="2026-08-21",
        group_overrides={
            group_id: {
                "image_enabled": False,
                "image_theme": "ai_free",
            }
        },
    )

    assert results == [
        {
            "group_name": "覆盖配置群",
            "status": "ready_to_send",
            "detail": "未启用生图",
        }
    ]
    run = pipeline.store.load_run("覆盖配置群", "2026-08-21")
    assert run["image_enabled"] is False
    assert run["image_theme"] == "ai_free"


def test_unexpected_worker_and_image_errors_are_isolated(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, generation_group_concurrency=3)
    groups = [
        Group(display_name="worker异常群", wechat_group_id="worker"),
        Group(display_name="图片异常群", wechat_group_id="image"),
        Group(display_name="正常群", wechat_group_id="ok"),
    ]
    pipeline = DailyPipeline(
        settings=settings,
        store=RunStore(tmp_path / "output"),
        dry_run=True,
    )
    monkeypatch.setattr(pipeline, "_load_groups", lambda group_ids=None: groups)

    def fake_generate(group, window, run_date, force):
        if group.display_name == "worker异常群":
            raise RuntimeError("worker boom")
        return {
            "group_name": group.display_name,
            "status": "prompt_ready" if group.display_name == "图片异常群" else "ready_to_send",
            "need_image": group.display_name == "图片异常群",
        }

    def fake_image(group, result, run_date, force):
        if group.display_name == "图片异常群":
            raise RuntimeError("image boom")
        return result

    monkeypatch.setattr(pipeline, "_generate_one_safe", fake_generate)
    monkeypatch.setattr(pipeline, "_run_image_when_ready", fake_image)

    results = pipeline.generate_all(run_date="2026-08-21")

    assert [item["group_name"] for item in results] == [
        "worker异常群",
        "图片异常群",
        "正常群",
    ]
    assert [item["status"] for item in results] == ["failed", "failed", "ready_to_send"]
    assert results[0]["error_type"] == "UNEXPECTED_GENERATION_ERROR"
    assert results[1]["error_type"] == "IMAGE_GENERATION_FAILED"


def test_shared_generation_lock_rejects_overlapping_task():
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with generation_mutex():
            acquired.set()
            release.wait(timeout=2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(timeout=1)
    try:
        with pytest.raises(GenerationBusyError, match="已有群报生成任务"):
            with generation_mutex(timeout_seconds=0.1):
                pass
    finally:
        release.set()
        thread.join(timeout=2)


def test_v1_group_workers_use_independent_database_sessions(tmp_path, monkeypatch):
    from sqlmodel import Session, SQLModel, create_engine

    engine = create_engine(
        f"sqlite:///{tmp_path / 'v1.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)
    with Session(engine) as main_session:
        groups = [
            repo.save_group(
                main_session,
                Group(display_name=f"V1群{index}", wechat_group_id=f"v1-{index}", enabled=True),
            )
            for index in range(5)
        ]
        held_sessions: list[Session] = []
        active = 0
        maximum = 0
        guard = threading.Lock()
        service = ReportService()

        def fake_generate_one(worker_session, run, group, window, force):
            nonlocal active, maximum
            held_sessions.append(worker_session)
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.05)
            with guard:
                active -= 1
            return GroupRun(
                run_id=run.id,
                group_id=group.id,
                ranking_status="success",
                prompt_status="success",
            )

        monkeypatch.setattr(service, "_generate_one", fake_generate_one)
        run = service.generate(
            main_session,
            report_date="2026-08-21",
            force=True,
        )

        assert run.status == "success"
        assert maximum > 1
        assert len(held_sessions) == len(groups)
        assert len({id(item) for item in held_sessions}) == len(groups)
        assert all(item is not main_session for item in held_sessions)
