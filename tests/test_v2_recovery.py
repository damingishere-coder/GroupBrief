"""V2 P9：恢复机制 / 启动检查 / 日志轮转单元测试。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.core.logging import clean_old_logs
from app.core.startup_check import run_startup_checks
from app.v2.constants import FAILED, IMAGE_READY, READY_TO_SEND, SENT
from app.v2.recovery import scan_incomplete, verify_output
from app.v2.run_store import RunStore


def _mk_run(store: RunStore, group: str, date: str, status: str, files: list[str] | None = None) -> dict:
    data = {"group_name": group, "run_date": date, "status": status, "updated_at": "2026-08-18 08:00:00"}
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

    monkeypatch.setattr(startup_check, "WeChatDataAnalysisSource", lambda settings=None: _FakeSource())
    monkeypatch.setattr(startup_check, "_tasklist_wechat", lambda: False)

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
    assert "DeepSeek V4 Flash" in names
    assert "输出目录" in names
    assert "模板资产" in names
    for c in checks:
        assert "ok" in c and "status" in c and "detail" in c
    # DeepSeek 未配置 → ok False
    ds = next(c for c in checks if c["name"] == "DeepSeek V4 Flash")
    assert ds["ok"] is False
    assert "未配置" in ds["detail"]
