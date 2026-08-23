from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import daily_auto


def test_daily_auto_partial_returns_nonzero(monkeypatch):
    monkeypatch.setattr(daily_auto, "_setup_logging", lambda: None)
    monkeypatch.setattr(daily_auto, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(daily_auto.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        daily_auto,
        "run_daily_v2_job",
        lambda *args, **kwargs: {"status": "partial"},
    )
    monkeypatch.setattr(sys, "argv", ["daily_auto.py", "--skip-email"])

    assert daily_auto.main() == 1


def test_daily_auto_success_returns_zero(monkeypatch):
    monkeypatch.setattr(daily_auto, "_setup_logging", lambda: None)
    monkeypatch.setattr(daily_auto, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(daily_auto.os, "chdir", lambda path: None)
    monkeypatch.setattr(
        daily_auto,
        "run_daily_v2_job",
        lambda *args, **kwargs: {"status": "success"},
    )
    monkeypatch.setattr(sys, "argv", ["daily_auto.py", "--skip-email"])

    assert daily_auto.main() == 0
