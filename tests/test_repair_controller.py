import json
import subprocess
from pathlib import Path

from app.config.settings import Settings
from app.repair.controller import CommandResult, RepairController
from app.repair.store import RepairIncidentStore


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, cwd, stdin="", timeout=300):
        self.calls.append((list(argv), Path(cwd), stdin, timeout))
        if argv[:3] == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
            return CommandResult(0, "refs/remotes/origin/master\n")
        if argv[:3] == ["git", "worktree", "add"]:
            Path(argv[5]).mkdir(parents=True)
            (Path(argv[5]) / "app" / "repair").mkdir(parents=True)
            (Path(argv[5]) / "app" / "repair" / "codex_result.schema.json").write_text("{}")
            return CommandResult(0)
        if len(argv) > 1 and argv[1] == "exec":
            result_path = Path(argv[argv.index("--output-last-message") + 1])
            result_path.write_text(json.dumps({
                "regression_test_added": True,
                "regression_test_reproduced": True,
                "tests_passed": True,
                "safe_to_propose_pr": True,
            }), encoding="utf-8")
            return CommandResult(0, '{"type":"thread.started","thread_id":"thread-test"}\n')
        if argv[:3] == ["git", "status", "--porcelain"]:
            return CommandResult(0, " M app/example.py\n?? tests/test_example.py\n")
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return CommandResult(0, "abc123\n")
        if argv[:3] == ["gh", "pr", "create"]:
            return CommandResult(0, "https://github.test/pr/1\n")
        return CommandResult(0, "2 passed\n")


class TimeoutRunner(FakeRunner):
    def run(self, argv, *, cwd, stdin="", timeout=300):
        if len(argv) > 1 and argv[1] == "exec":
            self.calls.append((list(argv), Path(cwd), stdin, timeout))
            raise subprocess.TimeoutExpired(argv, timeout)
        return super().run(argv, cwd=cwd, stdin=stdin, timeout=timeout)


def test_controller_uses_fixed_restricted_codex_command_and_only_creates_pr(tmp_path):
    settings = Settings(
        _env_file=None,
        output_root_override=str(tmp_path / "output"),
        repair_worktree_root=str(tmp_path / "worktrees"),
        repair_enabled=True,
    )
    store = RepairIncidentStore(settings)
    store.record(
        scope="ranking",
        error_type="RANKING_FAILED",
        stage="render",
        source_path="run.json",
        error_summary="authorization=top-secret",
    )
    runner = FakeRunner()
    repository = tmp_path / "repo"
    repository.mkdir()

    result = RepairController(
        settings,
        store=store,
        runner=runner,
        repository_root=repository,
    ).run_once()

    assert result["status"] == "pr_created"
    codex_argv, _, prompt, timeout = next(call for call in runner.calls if len(call[0]) > 1 and call[0][1] == "exec")
    assert ["--model", "gpt-5.6-sol"] == codex_argv[codex_argv.index("--model"):codex_argv.index("--model") + 2]
    for flag in ("--sandbox", "--approve-for-me", "--json", "--output-schema", "--ephemeral", "--ignore-user-config"):
        assert flag in codex_argv
    assert "top-secret" not in prompt
    assert timeout == 3600
    flattened = [call[0] for call in runner.calls]
    assert not any(argv[:3] == ["gh", "pr", "merge"] for argv in flattened)
    assert not any("deploy" in argv or "restart" in argv for argv in flattened)


def test_controller_timeout_is_persisted_and_never_pushes(tmp_path):
    settings = Settings(
        _env_file=None,
        output_root_override=str(tmp_path / "output"),
        repair_worktree_root=str(tmp_path / "worktrees"),
        repair_enabled=True,
        repair_timeout_minutes=1,
    )
    store = RepairIncidentStore(settings)
    store.record(
        scope="ranking",
        error_type="RANKING_TIMEOUT_TEST",
        stage="render",
        source_path="run.json",
    )
    runner = TimeoutRunner()
    repository = tmp_path / "repo"
    repository.mkdir()

    result = RepairController(
        settings,
        store=store,
        runner=runner,
        repository_root=repository,
    ).run_once()

    assert result["status"] == "failed"
    assert "1 分钟" in result["last_error"]
    assert not any(call[0][:2] == ["git", "push"] for call in runner.calls)
    assert not any(call[0][:3] == ["gh", "pr", "create"] for call in runner.calls)
