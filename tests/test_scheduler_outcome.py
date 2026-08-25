from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.scheduler.outcome import (
    SchedulerOutcomeError,
    outcome_for_status,
    require_scheduler_success,
    summarize_results,
)
from scripts import run_daily_pipeline as cli


@pytest.mark.parametrize(
    ("status", "outcome", "exit_code"),
    [
        ("success", "success", 0),
        ("already_completed", "success", 0),
        ("failed", "failed", 1),
        ("partial", "partial", 2),
        ("held", "blocked", 3),
        ("already_running", "already_running", 4),
        ("no_groups", "not_run", 5),
        ("unexpected-new-status", "failed", 1),
    ],
)
def test_stable_outcome_contract(status, outcome, exit_code):
    result = outcome_for_status(status)
    assert result == {"outcome_status": outcome, "exit_code": exit_code}


def test_result_aggregation_never_turns_unknown_or_mixed_failure_into_success():
    assert summarize_results([])["outcome_status"] == "not_run"
    assert summarize_results([{"status": "no_groups"}])["exit_code"] == 5
    assert summarize_results([{"status": "ready_to_send"}, {"status": "failed"}])[
        "outcome_status"
    ] == "partial"
    assert summarize_results([{"status": "sent"}, {"status": "held"}])[
        "outcome_status"
    ] == "blocked"
    assert summarize_results([{"status": "new-provider-state"}])["outcome_status"] == "failed"


def test_scheduler_rejects_business_failure_but_allows_periodic_no_work():
    with pytest.raises(SchedulerOutcomeError):
        require_scheduler_success(outcome_for_status("partial"))
    require_scheduler_success(outcome_for_status("not_run"), allow_not_run=True)


def test_system_status_exposes_configured_owner_and_actual_scheduler_state(monkeypatch):
    from app.api import system
    from app.config.settings import Settings

    monkeypatch.setattr(system.repo, "list_groups", lambda session, only_enabled=False: [])

    result = system.status(
        session=object(),
        settings=Settings(_env_file=None, scheduler_owner="external"),
    )

    assert result["scheduler_owner"] == "external"
    assert result["scheduler_active"] is False


class _FakeStore:
    def __init__(self, runs=None):
        self.runs = runs or []

    def list_runs(self):
        return self.runs


class _FakePipeline:
    def __init__(self, status: str, *, raises: bool = False):
        self.status = status
        self.raises = raises
        self.store = _FakeStore()

    def _result(self):
        if self.raises:
            raise RuntimeError("simulated failure")
        return {"group_name": "测试群", "status": self.status}

    def generate_all(self, **_kwargs):
        return [self._result()]

    def send_due(self):
        return [self._result()]

    def force_generate(self, *_args, **_kwargs):
        return self._result()

    def rebuild_prompt_from_snapshot(self, *_args, **_kwargs):
        return self._result()

    def force_send(self, *_args, **_kwargs):
        return self._result()


@pytest.mark.parametrize(
    ("argv", "status", "expected"),
    [
        (["run_daily_pipeline.py", "generate"], "partial", 2),
        (["run_daily_pipeline.py", "send"], "held", 3),
        (["run_daily_pipeline.py", "force-generate", "--group", "1"], "failed", 1),
        (
            ["run_daily_pipeline.py", "rebuild-prompt", "--group", "1", "--date", "2026-08-25"],
            "blocked",
            3,
        ),
        (["run_daily_pipeline.py", "force-send", "--group", "1"], "sent", 0),
    ],
)
def test_pipeline_cli_propagates_business_outcome(monkeypatch, capsys, argv, status, expected):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "_pipeline", lambda dry_run=False: _FakePipeline(status))

    assert cli.main() == expected
    assert f'"exit_code": {expected}' in capsys.readouterr().out


def test_pipeline_cli_reports_unhandled_action_exception_as_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["run_daily_pipeline.py", "send"])
    monkeypatch.setattr(cli, "_pipeline", lambda dry_run=False: _FakePipeline("sent", raises=True))

    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert '"outcome_status": "failed"' in output


def test_pipeline_status_blocks_when_any_run_state_is_corrupt(monkeypatch):
    pipeline = _FakePipeline("success")
    pipeline.store = _FakeStore([{"status": "CORRUPT"}])
    args = SimpleNamespace(cmd="status")

    assert cli._execute(args, pipeline) == 3
