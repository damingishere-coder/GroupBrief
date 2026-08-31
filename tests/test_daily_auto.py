from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from scripts import daily_auto


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", 0),
        ("already_completed", 0),
        ("failed", 1),
        ("partial", 2),
        ("blocked", 3),
        ("held", 3),
        ("already_running", 4),
        ("no_groups", 5),
    ],
)
def test_daily_auto_uses_stable_outcome_exit_codes(monkeypatch, capsys, status, expected):
    monkeypatch.setattr(daily_auto, "_setup_logging", lambda: None)
    monkeypatch.setattr(daily_auto, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(daily_auto.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        daily_auto,
        "run_daily_v2_job",
        lambda *args, **kwargs: {"status": status},
    )
    monkeypatch.setattr(sys, "argv", ["daily_auto.py", "--skip-email"])

    assert daily_auto.main() == expected
    assert f'"exit_code": {expected}' in capsys.readouterr().out
