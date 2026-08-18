"""V2 P6：WechatAutomationSender 单元测试。

通过注入假 CLI 路径与 monkeypatch subprocess 隔离真实微信，
验证：dry_run 不调用外部 / 图片校验 / CLI 不可用 / JSON 结果解析 /
微信进程检测。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sender.wechat_automation import WechatAutomationSender


def _sender(tmp_path, dry_run=False, cli_path=None, **kw) -> WechatAutomationSender:
    cli = cli_path or str(tmp_path / "skill_cli.py")
    if not Path(cli).exists():
        Path(cli).write_text("", encoding="utf-8")
    return WechatAutomationSender(
        cli_path=cli,
        python_path="python",
        dry_run=dry_run,
        **kw,
    )


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_dry_run_send_text_no_external(tmp_path, monkeypatch):
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return FakeProc()

    monkeypatch.setattr("subprocess.run", fake_run)
    s = _sender(tmp_path, dry_run=True)
    r = s.send_text("文件传输助手", "你好")
    assert r.success is True
    assert "[dry_run]" in r.detail
    assert called["n"] == 0  # 未调用外部


def test_dry_run_send_image(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG")
    s = _sender(tmp_path, dry_run=True)
    r = s.send_image("文件传输助手", img)
    assert r.success is True
    assert "[dry_run]" in r.detail


def test_send_image_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc())
    s = _sender(tmp_path, dry_run=False)
    r = s.send_image("文件传输助手", tmp_path / "nope.png")
    assert r.success is False
    assert "不存在" in r.detail


def test_send_parses_json_success(tmp_path, monkeypatch):
    payload = json.dumps({"success": True, "message": "发送成功", "code": "OK"}, ensure_ascii=False)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc(stdout=payload))
    s = _sender(tmp_path, dry_run=False)
    r = s.send_text("文件传输助手", "hi")
    assert r.success is True
    assert "发送成功" in r.detail


def test_send_parses_json_failure(tmp_path, monkeypatch):
    payload = json.dumps({"success": False, "code": "WECHAT_WINDOW_NOT_FOUND", "message": "未找到微信窗口"}, ensure_ascii=False)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc(stdout=payload))
    s = _sender(tmp_path, dry_run=False)
    r = s.send_text("文件传输助手", "hi")
    assert r.success is False
    assert "WECHAT_WINDOW_NOT_FOUND" in r.detail


def test_send_uses_absolute_image_path(tmp_path, monkeypatch):
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        return FakeProc(stdout='{"success": true}')

    monkeypatch.setattr("subprocess.run", fake_run)
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG")
    s = _sender(tmp_path, dry_run=False)
    s.send_image("文件传输助手", img)
    assert "sendpic" in captured["cmd"]
    assert str(img.resolve()) in captured["cmd"]  # 使用绝对路径


def test_health_missing_cli():
    s = WechatAutomationSender(cli_path="C:/nonexistent/skill_cli.py")
    ok, detail = s.health_check()
    assert ok is False
    assert "CLI 不存在" in detail


def test_health_wechat_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.run", lambda *a, **k: FakeProc(stdout="INFO: No tasks are running"))
    s = _sender(tmp_path)
    ok, detail = s.health_check()
    assert ok is False
    assert "Weixin.exe" in detail or "CLI" in detail or "OFFLINE" in detail or "探测" in detail
