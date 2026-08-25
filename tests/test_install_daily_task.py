from __future__ import annotations

import sys
from pathlib import Path

from scripts import install_daily_task as installer


def _task_xml(enabled: bool) -> str:
    value = "true" if enabled else "false"
    return f'<?xml version="1.0" encoding="UTF-16"?><Task><Settings><Enabled>{value}</Enabled></Settings></Task>'


def test_install_refuses_to_create_second_owner(monkeypatch):
    monkeypatch.setattr(
        installer,
        "_run",
        lambda args: (_ for _ in ()).throw(AssertionError("owner 冲突时不得调用 schtasks")),
    )

    code, message = installer._install(owner="fastapi")

    assert code == 3
    assert "拒绝创建" in message


def test_external_install_creates_generation_and_repeating_send_tasks(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "PYTHON_EXE", Path(sys.executable))
    monkeypatch.setattr(installer, "_run", lambda args: (calls.append(args) or (0, "ok")))

    code, message = installer._install(owner="external")

    assert code == 0
    assert "00:15" in message
    assert "08:30" in message
    assert len(calls) == 2
    assert calls[0][calls[0].index("/TN") + 1] == installer.GENERATE_TASK_NAME
    assert calls[1][calls[1].index("/TN") + 1] == installer.SEND_TASK_NAME
    assert calls[1][calls[1].index("/RI") + 1] == "1"
    assert calls[1][calls[1].index("/DU") + 1] == "00:30"


def test_install_rolls_back_generation_when_send_task_fails(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "PYTHON_EXE", Path(sys.executable))

    def fake_run(args):
        calls.append(args)
        if "/Create" in args and installer.SEND_TASK_NAME in args:
            return 1, "send failed"
        return 0, "ok"

    monkeypatch.setattr(installer, "_run", fake_run)

    code, message = installer._install(owner="external")

    assert code == 1
    assert "已回滚生成任务" in message
    assert calls[-1] == ["schtasks", "/Delete", "/TN", installer.GENERATE_TASK_NAME, "/F"]


def test_status_detects_enabled_legacy_tasks_as_fastapi_owner_conflict(monkeypatch):
    monkeypatch.setattr(installer, "_run", lambda args: (0, _task_xml(True)))

    code, message = installer._status(owner="fastapi")

    assert code == 3
    assert "outcome=blocked" in message


def test_task_xml_without_enabled_uses_windows_default_true(monkeypatch):
    monkeypatch.setattr(installer, "_run", lambda args: (0, "<Task><Settings /></Task>"))

    assert installer._query_enabled(installer.GENERATE_TASK_NAME) == (True, True, "")


def test_status_accepts_disabled_legacy_tasks_for_fastapi_owner(monkeypatch):
    monkeypatch.setattr(installer, "_run", lambda args: (0, _task_xml(False)))

    code, message = installer._status(owner="fastapi")

    assert code == 0
    assert "outcome=success" in message


def test_enable_requires_external_owner(monkeypatch):
    monkeypatch.setattr(
        installer,
        "_run",
        lambda args: (_ for _ in ()).throw(AssertionError("owner 冲突时不得启用任务")),
    )

    code, _ = installer._set_enabled(True, owner="fastapi")

    assert code == 3


def test_uninstall_attempts_both_tasks(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "_run", lambda args: (calls.append(args) or (0, "ok")))

    code, message = installer._uninstall()

    assert code == 0
    assert installer.GENERATE_TASK_NAME in message
    assert installer.SEND_TASK_NAME in message
    assert [call[call.index("/TN") + 1] for call in calls] == [
        installer.GENERATE_TASK_NAME,
        installer.SEND_TASK_NAME,
    ]
