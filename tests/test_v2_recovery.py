"""V2 P9：恢复机制 / 启动检查 / 日志轮转单元测试。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.core.logging import clean_old_logs
from app.core.startup_check import run_startup_checks
from app.v2.constants import CORRUPT, FAILED, IMAGE_READY, READY_TO_SEND, SENT
from app.v2.recovery import recover_incomplete, scan_incomplete, verify_output
from app.v2.run_store import RunStateCorruptionError, RunStore


def _mk_run(
    store: RunStore,
    group: str,
    date: str,
    status: str,
    files: list[str] | None = None,
    image_enabled: bool | None = None,
) -> dict:
    data = {"group_name": group, "run_date": date, "status": status, "updated_at": "2026-08-18 08:00:00"}
    if image_enabled is not None:
        data["image_enabled"] = image_enabled
    store.save_run(group, date, data)
    if files:
        d = store.group_dir(group, date)
        for f in files:
            (d / f).write_text("x", encoding="utf-8")
    return data


def test_scan_incomplete_excludes_terminal(tmp_path):
    store = RunStore(tmp_path / "output")
    _mk_run(store, "群A", "2026-08-18", READY_TO_SEND, ["ranking.txt"])
    _mk_run(store, "群B", "2026-08-18", SENT, ["ranking.txt"])
    _mk_run(store, "群C", "2026-08-18", FAILED, ["ranking.txt"])
    _mk_run(store, "群D", "2026-08-18", "DATA_READY", ["messages.json"])
    incomplete = scan_incomplete(store)
    by_name = {r["group_name"]: r for r in incomplete}
    assert set(by_name) == {"群A", "群D"}  # SENT/FAILED 排除；READY_TO_SEND 归类为 send
    assert by_name["群A"]["recovery_type"] == "send"
    assert by_name["群D"]["recovery_type"] == "generate"


def test_verify_output_reports_missing(tmp_path):
    store = RunStore(tmp_path / "output")
    _mk_run(store, "群A", "2026-08-18", READY_TO_SEND, ["ranking.txt", "image_prompt.txt"])
    # 缺 daily_image.png / messages.json / ranking.json
    results = verify_output(store)
    r = next(x for x in results if x["group_name"] == "群A")
    assert r["ok"] is False
    assert "daily_image.png" in r["missing"]
    assert "messages.json" in r["missing"]


def test_verify_output_complete(tmp_path):
    store = RunStore(tmp_path / "output")
    files = ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt", "daily_image.png"]
    _mk_run(store, "群A", "2026-08-18", SENT, files)
    results = verify_output(store)
    assert results[0]["ok"] is True
    assert results[0]["missing"] == []


def test_verify_output_image_disabled_does_not_require_image(tmp_path):
    store = RunStore(tmp_path / "output")
    files = ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"]
    _mk_run(store, "群A", "2026-08-18", READY_TO_SEND, files, image_enabled=False)
    results = verify_output(store)
    assert results[0]["ok"] is True
    assert results[0]["missing"] == []


def test_verify_output_image_enabled_requires_image(tmp_path):
    store = RunStore(tmp_path / "output")
    files = ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"]
    _mk_run(store, "群A", "2026-08-18", SENT, files, image_enabled=True)
    results = verify_output(store)
    assert results[0]["ok"] is False
    assert results[0]["missing"] == ["daily_image.png"]


def test_verify_output_legacy_run_conservatively_requires_image(tmp_path):
    store = RunStore(tmp_path / "output")
    files = ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"]
    _mk_run(store, "群A", "2026-08-18", SENT, files)
    results = verify_output(store)
    assert results[0]["ok"] is False
    assert results[0]["missing"] == ["daily_image.png"]


@pytest.mark.parametrize("bad_date", ["..", "2026-02-30", "2026-8-18", "not-a-date"])
def test_run_store_rejects_invalid_dates(tmp_path, bad_date):
    store = RunStore(tmp_path / "output")
    with pytest.raises(ValueError):
        store.group_dir("群A", bad_date)
    with pytest.raises(ValueError):
        store.list_runs(bad_date)


def test_recent_layout_history_skips_legacy_and_corrupt_runs_without_rewriting(tmp_path):
    store = RunStore(tmp_path / "output")
    store.save_run(
        "群A",
        "2026-08-19",
        {"prompt_meta": {"layout_id": "hero_cover", "comedy_device": "字面化"}},
    )
    store.save_run("群A", "2026-08-20", {"status": "SENT"})  # 旧版没有 prompt_meta
    corrupt_path = store.run_path("群A", "2026-08-21")
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("{broken", encoding="utf-8")
    before = corrupt_path.read_text(encoding="utf-8")

    history = store.recent_layout_history("群A", "2026-08-22", limit=3)

    assert history == (
        {
            "run_date": "2026-08-19",
            "layout_id": "hero_cover",
            "comedy_device": "字面化",
            "layout_signature": "",
        },
    )
    assert corrupt_path.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "raw",
    [b"", b"{broken", b"null", b"[]", b'"text"'],
    ids=["empty", "truncated", "null", "array", "string"],
)
def test_corrupt_run_is_not_treated_as_pending_or_overwritten(tmp_path, raw):
    store = RunStore(tmp_path / "output")
    path = store.run_path("群A", "2026-08-21")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)

    run = store.load_run("群A", "2026-08-21")

    assert run["status"] == CORRUPT
    assert run["error_type"] == "RUN_STATE_CORRUPT"
    assert run["needs_manual_review"] is True
    assert "{broken" not in str(run)
    with pytest.raises(RunStateCorruptionError):
        store.update("群A", "2026-08-21", status=READY_TO_SEND)
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "payload",
    [
        {"group_name": "群A", "run_date": "2026-08-21"},
        {"group_name": "群A", "run_date": "wrong", "status": READY_TO_SEND},
        {"group_name": None, "run_date": "2026-08-21", "status": READY_TO_SEND},
        {"group_name": "群A", "run_date": "2026-08-21", "status": []},
    ],
    ids=["missing-status", "wrong-date", "bad-group", "bad-status"],
)
def test_run_schema_corruption_requires_manual_review(tmp_path, payload):
    store = RunStore(tmp_path / "output")
    path = store.run_path("群A", "2026-08-21")
    path.parent.mkdir(parents=True)
    original = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(original)

    listed = store.list_runs("2026-08-21")
    incomplete = scan_incomplete(store, "2026-08-21")
    integrity = verify_output(store, "2026-08-21")

    assert listed[0]["status"] == CORRUPT
    assert incomplete[0]["recovery_type"] == "manual_review"
    assert integrity[0]["ok"] is False
    assert integrity[0]["error_type"] == "RUN_STATE_CORRUPT"
    assert path.read_bytes() == original


def test_recovery_never_executes_corrupt_run(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "output")
    path = store.run_path("群A", "2026-08-21")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    from app.pipeline import daily_pipeline

    monkeypatch.setattr(
        daily_pipeline,
        "DailyPipeline",
        lambda: pytest.fail("损坏状态不得构造自动恢复 Pipeline"),
    )

    result = recover_incomplete(store, run_date="2026-08-21")

    assert result == [
        {
            "group_name": "群A",
            "status": "blocked",
            "error_type": "RUN_STATE_CORRUPT",
            "detail": "运行状态文件损坏，需人工复核",
        }
    ]


def test_retry_api_blocks_corrupt_run_before_group_lookup(tmp_path, monkeypatch):
    from app.api import v2_ui
    from app.pipeline import daily_pipeline

    store = RunStore(tmp_path / "output")
    path = store.run_path("群A", "2026-08-21")
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")

    class FakeSettings:
        output_dir = tmp_path / "output"

    monkeypatch.setattr(v2_ui, "_store", lambda settings: store)
    monkeypatch.setattr(
        daily_pipeline,
        "DailyPipeline",
        lambda: pytest.fail("损坏状态不得构造自动恢复 Pipeline"),
    )

    response = v2_ui.retry_failed(
        v2_ui.RetryBody(run_date="2026-08-21"),
        settings=FakeSettings(),
    )

    assert response["results"] == [
        {
            "group_name": "群A",
            "status": "blocked",
            "error_type": "RUN_STATE_CORRUPT",
            "detail": "运行状态文件损坏，需人工复核",
        }
    ]


def test_clean_old_logs_removes_expired(tmp_path):
    old = tmp_path / "old.log"
    old.write_text("x", encoding="utf-8")
    old_time = time.time() - 40 * 86400
    os.utime(old, (old_time, old_time))
    new = tmp_path / "new.log"
    new.write_text("x", encoding="utf-8")
    removed = clean_old_logs(tmp_path, max_days=30)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


class _FakeHealth:
    ok = False
    status = "UNAVAILABLE"
    detail = "test-detail"


class _FakeSource:
    def health_check(self):
        return _FakeHealth()


def test_startup_checks_structure(tmp_path, monkeypatch):
    """外部调用（WeChatDataAnalysis / tasklist）注入替身，全本地不触网。"""
    from app.core import startup_check
    from app.providers.ai import codex as codex_provider

    monkeypatch.setattr(startup_check, "WeChatDataAnalysisSource", lambda settings=None: _FakeSource())
    monkeypatch.setattr(startup_check, "_tasklist_wechat", lambda: False)

    class FakeCodex:
        def health_check(self):
            return True, "主模型 gpt-5.6-sol 可用"

    monkeypatch.setattr(codex_provider, "CodexGPTProvider", lambda settings: FakeCodex())

    # Settings 的 output_dir 为只读 property，用鸭子类型对象替代
    class FakeSettings:
        ai_api_key = ""
        ai_model = "deepseek-chat"
        output_dir = tmp_path / "output"

        def ensure_dirs(self):
            self.output_dir.mkdir(parents=True, exist_ok=True)

    settings = FakeSettings()
    checks = run_startup_checks(settings)
    names = {c["name"] for c in checks}
    assert "WeChatDataAnalysis 数据源" in names
    assert "微信客户端" in names
    assert "Codex GPT 群聊总结" in names
    assert "DeepSeek V4 Flash（备用）" in names
    assert "输出目录" in names
    assert "模板资产" in names
    for c in checks:
        assert "ok" in c and "status" in c and "detail" in c
    # DeepSeek 未配置 → ok False
    ds = next(c for c in checks if c["name"] == "DeepSeek V4 Flash（备用）")
    assert ds["ok"] is False
    assert "未配置" in ds["detail"]
    codex = next(c for c in checks if c["name"] == "Codex GPT 群聊总结")
    assert codex["ok"] is True
