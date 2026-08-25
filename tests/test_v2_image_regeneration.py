from __future__ import annotations

import threading
import time
import hashlib
from types import SimpleNamespace

import pytest

from app.image.image_task import ImageTaskResult
from app.image.regeneration import enqueue_regeneration, run_regeneration_now
from app.image.regeneration import claim_regeneration_candidate, list_regeneration_candidates
from app.v2.run_store import RunStore

OLD_PNG = b"\x89PNG\r\n\x1a\nold-image"
NEW_PNG = b"\x89PNG\r\n\x1a\nnew-image"


class SuccessGenerator:
    def generate(self, prompt_path, output_path):
        assert "已保存的当天 Prompt" in prompt_path.read_text(encoding="utf-8")
        output_path.write_bytes(NEW_PNG)
        return ImageTaskResult(True, image_path=output_path)


class FailureGenerator:
    def __init__(self, error: str, stage: str):
        self.error = error
        self.stage = stage

    def generate(self, prompt_path, output_path):
        return ImageTaskResult(False, error=self.error, detail={"stage": self.stage})


class BlockingGenerator:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(self, prompt_path, output_path):
        self.started.set()
        self.release.wait(timeout=3)
        output_path.write_bytes(NEW_PNG)
        return ImageTaskResult(True, image_path=output_path)


def _run(tmp_path, *, status="SENT"):
    settings = SimpleNamespace(output_dir=tmp_path, image_generation_concurrency=2)
    store = RunStore(tmp_path)
    group, run_date = "重新生图测试群", "2026-08-21"
    store.save_run(group, run_date, {"status": status, "sent_at": "2026-08-21 09:00:00"})
    store.prompt_path(group, run_date).write_text("已保存的当天 Prompt", encoding="utf-8")
    store.image_path(group, run_date).write_bytes(OLD_PNG)
    return settings, store, group, run_date


def test_success_atomically_replaces_image_backs_up_old_and_holds_send(tmp_path):
    settings, store, group, run_date = _run(tmp_path)

    run = run_regeneration_now(settings, group, run_date, SuccessGenerator())

    assert store.image_path(group, run_date).read_bytes() == NEW_PNG
    assert store.previous_image_path(group, run_date).read_bytes() == OLD_PNG
    assert run["status"] == "SENT"
    assert run["image_regen_status"] == "ready_for_review"
    assert run["send_hold"] is True
    assert run["needs_manual_send"] is True
    assert run["sent_at"] == "2026-08-21 09:00:00"


def test_cli_failure_keeps_old_image_and_fails_closed(tmp_path):
    settings, store, group, run_date = _run(tmp_path)

    run = run_regeneration_now(settings, group, run_date, FailureGenerator("codex CLI 不可用", "health"))

    assert store.image_path(group, run_date).read_bytes() == OLD_PNG
    assert not store.previous_image_path(group, run_date).exists()
    assert run["image_regen_status"] == "failed"
    assert run["desktop_regen_requested"] is False
    assert run["send_hold"] is True


def test_policy_rejection_does_not_fallback_and_keeps_old_image(tmp_path):
    settings, store, group, run_date = _run(tmp_path)

    run = run_regeneration_now(settings, group, run_date, FailureGenerator("安全策略拒绝：Prompt 违规", "exec"))

    assert store.image_path(group, run_date).read_bytes() == OLD_PNG
    assert run["image_regen_status"] == "failed"
    assert run["desktop_regen_requested"] is False
    assert run["send_hold"] is True


def test_duplicate_click_is_rejected_while_same_run_is_active(tmp_path):
    settings, store, group, run_date = _run(tmp_path, status="READY_TO_SEND")
    generator = BlockingGenerator()

    enqueue_regeneration(settings, group, run_date, generator=generator)
    assert generator.started.wait(timeout=2)
    with pytest.raises(RuntimeError, match="队列"):
        enqueue_regeneration(settings, group, run_date, generator=generator)
    generator.release.set()

    deadline = time.time() + 3
    while time.time() < deadline:
        if store.load_run(group, run_date).get("image_regen_status") == "ready_for_review":
            break
        time.sleep(0.05)
    assert store.load_run(group, run_date)["image_regen_status"] == "ready_for_review"


def test_two_different_runs_can_regenerate_concurrently(tmp_path):
    settings = SimpleNamespace(output_dir=tmp_path, image_generation_concurrency=2)
    store = RunStore(tmp_path)
    guard = threading.Lock()

    class ConcurrentGenerator:
        def __init__(self):
            self.active = 0
            self.maximum = 0

        def generate(self, prompt_path, output_path):
            with guard:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            time.sleep(0.12)
            output_path.write_bytes(NEW_PNG)
            with guard:
                self.active -= 1
            return ImageTaskResult(True, image_path=output_path)

    generator = ConcurrentGenerator()
    for group in ("并发群A", "并发群B"):
        store.save_run(group, "2026-08-21", {"status": "SENT"})
        store.prompt_path(group, "2026-08-21").write_text("真实 Prompt", encoding="utf-8")
        store.image_path(group, "2026-08-21").write_bytes(OLD_PNG)
        enqueue_regeneration(settings, group, "2026-08-21", generator=generator)

    deadline = time.time() + 3
    while time.time() < deadline:
        states = [
            store.load_run(group, "2026-08-21").get("image_regen_status")
            for group in ("并发群A", "并发群B")
        ]
        if states == ["ready_for_review", "ready_for_review"]:
            break
        time.sleep(0.03)
    assert generator.maximum == 2
    assert states == ["ready_for_review", "ready_for_review"]


def test_ambiguous_candidate_requires_explicit_job_and_hash_claim(tmp_path):
    settings = SimpleNamespace(
        output_dir=tmp_path,
        codex_path="",
        codex_timeout_seconds=1200,
        codex_home=str(tmp_path / "codex-home"),
        codex_generated_images_dir="",
        image_generation_concurrency=2,
    )
    store = RunStore(tmp_path)
    group, run_date, job_id = "候选认领群", "2026-08-21", "job-candidate-001"
    store.save_run(
        group,
        run_date,
        {
            "status": "SENT",
            "sent_at": "2026-08-21 09:00:00",
            "group_id": 23,
            "wechat_group_id": "wx-group-23",
        },
    )
    store.prompt_path(group, run_date).write_text("真实 Prompt", encoding="utf-8")
    store.image_path(group, run_date).write_bytes(OLD_PNG)
    candidate = store.group_dir(group, run_date) / ".imagegen-jobs" / job_id / "candidate.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(NEW_PNG)
    digest = hashlib.sha256(NEW_PNG).hexdigest().upper()
    store.update(
        group,
        run_date,
        image_regen_status="ambiguous_result",
        image_regen_job={
            "job_id": job_id,
            "revision": 1,
            "prompt_sha256": "a" * 64,
            "status": "ambiguous_result",
            "candidates": [{
                "candidate_id": digest.lower(),
                "sha256": digest,
                "root": "task",
                "relative_path": f".imagegen-jobs/{job_id}/candidate.png",
                "size_bytes": len(NEW_PNG),
                "sources": ["scan"],
            }],
        },
    )

    candidates = list_regeneration_candidates(settings, group, run_date)
    assert [item["candidate_id"] for item in candidates] == [digest.lower()]
    with pytest.raises(ValueError, match="job_id"):
        claim_regeneration_candidate(
            settings,
            group,
            run_date,
            job_id="wrong-job",
            candidate_id=digest.lower(),
        )

    run = claim_regeneration_candidate(
        settings,
        group,
        run_date,
        job_id=job_id,
        candidate_id=digest.lower(),
    )
    assert store.image_path(group, run_date).read_bytes() == NEW_PNG
    assert store.previous_image_path(group, run_date).read_bytes() == OLD_PNG
    assert run["status"] == "SENT"
    assert run["sent_at"] == "2026-08-21 09:00:00"
    assert run["image_regen_status"] == "ready_for_review"
    assert run["send_hold"] is True
