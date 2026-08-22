from __future__ import annotations

import sys
from pathlib import Path

from scripts import install_daily_task as installer


def test_install_creates_generation_and_repeating_send_tasks(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(installer, "PYTHON_EXE", Path(sys.executable))
    monkeypatch.setattr(
        installer,
        "_run",
        lambda args: (calls.append(args) or (0, "ok")),
    )

    result = installer._install()

    assert "00:15" in result
    assert "08:30" in result
    assert len(calls) == 2
    assert calls[0][calls[0].index("/TN") + 1] == installer.GENERATE_TASK_NAME
    assert calls[0][calls[0].index("/ST") + 1] == "00:15"
    assert calls[1][calls[1].index("/TN") + 1] == installer.SEND_TASK_NAME
    assert calls[1][calls[1].index("/ST") + 1] == "08:30"
    assert calls[1][calls[1].index("/RI") + 1] == "1"
    assert calls[1][calls[1].index("/DU") + 1] == "00:30"


def test_uninstall_attempts_both_tasks(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        installer,
        "_run",
        lambda args: (calls.append(args) or (0, "ok")),
    )

    result = installer._uninstall()

    assert installer.GENERATE_TASK_NAME in result
    assert installer.SEND_TASK_NAME in result
    assert [call[call.index("/TN") + 1] for call in calls] == [
        installer.GENERATE_TASK_NAME,
        installer.SEND_TASK_NAME,
    ]
