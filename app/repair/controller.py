"""独立 Codex 维修控制器；只在隔离 worktree 产出待确认 PR。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config.settings import PROJECT_ROOT, Settings
from app.repair.store import RepairIncidentStore, sanitize_text
from app.v2.run_store import _run_mutex

_ALLOWED_PREFIXES = ("app/", "tests/", "frontend/", "scripts/", "docs/", ".env.example")
_SENSITIVE_PARTS = (
    ".env", "auth.json", "cookie", "token", "credential", "secret", "browser-data",
)
_TESTS_BY_SCOPE = {
    "ranking": ["tests/test_v2_pipeline.py"],
    "data": ["tests/test_v2_pipeline.py", "tests/test_v2_data_source.py"],
    "prompt": ["tests/test_v2_prompt_builder.py", "tests/test_v2_pipeline.py"],
    "image": ["tests/test_v2_image_task.py", "tests/test_v2_pipeline.py"],
    "weekly": ["tests/test_weekly_insights.py"],
    "scheduler": ["tests/test_scheduler.py", "tests/test_reliability_watchdog.py"],
}


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner:
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str = "",
        timeout: int = 300,
    ) -> CommandResult:
        environment = os.environ.copy()
        executable = Path(argv[0]).name.lower() if argv else ""
        if executable not in {"git", "git.exe", "gh", "gh.exe"}:
            for key in tuple(environment):
                upper = key.upper()
                if upper in {"OPENAI_API_KEY", "CODEX_API_KEY"} or any(
                    marker in upper for marker in ("COOKIE", "TOKEN", "PASSWORD")
                ):
                    environment.pop(key, None)
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            input=stdin or None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=environment,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout[-20000:],
            completed.stderr[-12000:],
        )


class RepairController:
    def __init__(
        self,
        settings: Settings,
        *,
        store: RepairIncidentStore | None = None,
        runner: CommandRunner | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or RepairIncidentStore(settings)
        self.runner = runner or CommandRunner()
        self.repository_root = (repository_root or PROJECT_ROOT).resolve()

    def _run(self, argv: list[str], *, cwd: Path, stdin: str = "", timeout: int = 300) -> CommandResult:
        result = self.runner.run(argv, cwd=cwd, stdin=stdin, timeout=timeout)
        if result.returncode != 0:
            detail = sanitize_text(result.stderr or result.stdout, limit=800)
            raise RuntimeError(f"命令失败({argv[0]} {argv[1] if len(argv) > 1 else ''})：{detail}")
        return result

    @staticmethod
    def _default_branch(symbolic: str) -> str:
        match = re.search(r"refs/remotes/origin/([^\s]+)", symbolic)
        if not match:
            raise RuntimeError("无法解析 origin/HEAD 默认分支")
        return match.group(1)

    def _prompt(self, incident: dict, tests: list[str]) -> str:
        payload = {
            "fingerprint": incident["fingerprint"],
            "scope": incident["scope"],
            "error_type": incident["error_type"],
            "stage": incident["stage"],
            "source_path": incident.get("source_path", ""),
            "error_summary": incident.get("redacted_error_summary", ""),
            "related_commit_sha": incident.get("related_commit_sha", ""),
            "required_tests": tests,
        }
        return (
            "你在隔离 Git worktree 中修复 GroupBrief 的一个确定性代码故障。"
            "只能修改 app/、tests/、frontend/、scripts/、docs/ 或 .env.example；"
            "必须先新增能复现问题的回归测试，再做最小修复并运行指定测试。"
            "结构化结果必须明确回归测试在修复前已复现、修复后已通过。"
            "禁止读取 .env、认证文件、原始群聊、Cookie、Token、微信截图；"
            "禁止发送消息、部署、重启、提交、Push、创建或合并 PR。"
            "最终严格按输出 Schema 返回。\n故障："
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

    @staticmethod
    def _changed_paths(porcelain: str) -> list[str]:
        paths: list[str] = []
        for line in porcelain.splitlines():
            if len(line) < 4:
                continue
            value = line[3:].strip().replace("\\", "/")
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            paths.append(value.strip('"'))
        return sorted(set(paths))

    @staticmethod
    def _audit_paths(paths: list[str]) -> None:
        if not paths:
            raise RuntimeError("Codex 未产生任何修改")
        for path in paths:
            lowered = path.lower()
            if ".." in Path(path).parts or any(part in lowered for part in _SENSITIVE_PARTS):
                raise RuntimeError(f"检测到敏感或越界文件：{path}")
            if not (
                path == ".env.example"
                or any(path.startswith(prefix) for prefix in _ALLOWED_PREFIXES[:-1])
            ):
                raise RuntimeError(f"检测到范围外修改：{path}")
        if not any(path.startswith("tests/") or path.endswith(".test.ts") or "/e2e/" in path for path in paths):
            raise RuntimeError("未新增或修改回归测试")

    @staticmethod
    def _audit_diff(diff: str) -> None:
        added = "\n".join(
            line[1:] for line in diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        secret_patterns = (
            r"sk-[A-Za-z0-9_-]{20,}",
            r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}",
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"(?i)(?:api[_-]?key|password|cookie|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]",
        )
        if any(re.search(pattern, added) for pattern in secret_patterns):
            raise RuntimeError("diff 中疑似包含敏感数据")

    def run_once(self) -> dict:
        if not self.settings.repair_enabled:
            return {"status": "disabled"}
        with _run_mutex(self.store.root / "controller-process.lock"):
            incident, reason = self.store.start_next()
            if incident is None:
                return {"status": "not_run", "reason": reason}
            try:
                return self._execute(incident)
            except subprocess.TimeoutExpired:
                timeout_minutes = max(min(int(self.settings.repair_timeout_minutes), 60), 1)
                return self.store.finish(
                    incident,
                    success=False,
                    reason=f"维修任务超过 {timeout_minutes} 分钟",
                )
            except Exception as exc:
                return self.store.finish(incident, success=False, reason=str(exc))

    def _execute(self, incident: dict) -> dict:
        tests = _TESTS_BY_SCOPE.get(str(incident.get("scope") or ""), ["tests/test_scheduler.py"])
        self._run(["git", "fetch", "--prune", "origin"], cwd=self.repository_root)
        symbolic = self._run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=self.repository_root,
        ).stdout
        base = self._default_branch(symbolic)
        branch = f"codex/fix-auto-{incident['fingerprint'][:10]}-{incident['incident_id'][:6]}"
        worktree = (self.settings.repair_worktrees_dir / incident["incident_id"]).resolve()
        root = self.settings.repair_worktrees_dir.resolve()
        if root not in worktree.parents or worktree.exists():
            raise RuntimeError("维修 worktree 路径不安全或已存在")
        root.mkdir(parents=True, exist_ok=True)
        self._run(
            ["git", "worktree", "add", "-b", branch, str(worktree), f"origin/{base}"],
            cwd=self.repository_root,
        )
        incident["branch"] = branch
        self.store.save(incident)

        result_path = worktree / ".codex-repair-result.json"
        schema_path = worktree / "app" / "repair" / "codex_result.schema.json"
        command = [
            self.settings.repair_codex_binary,
            "exec",
            "--model", "gpt-5.6-sol",
            "--sandbox", "workspace-write",
            "--approve-for-me",
            "--json",
            "--output-schema", str(schema_path),
            "--output-last-message", str(result_path),
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--cd", str(worktree),
            "-",
        ]
        codex = self._run(
            command,
            cwd=worktree,
            stdin=self._prompt(incident, tests),
            timeout=max(min(int(self.settings.repair_timeout_minutes), 60), 1) * 60,
        )
        thread_match = re.search(r'"type"\s*:\s*"thread.started".*?"thread_id"\s*:\s*"([^"]+)"', codex.stdout)
        incident["codex_thread_id"] = thread_match.group(1) if thread_match else ""
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Codex 结构化结果缺失或无效") from exc
        finally:
            if result_path.exists():
                result_path.unlink()
        if not all(
            bool(result.get(key))
            for key in (
                "regression_test_added",
                "regression_test_reproduced",
                "tests_passed",
                "safe_to_propose_pr",
            )
        ):
            raise RuntimeError("Codex 未满足回归测试与安全 PR 条件")

        status = self._run(["git", "status", "--porcelain"], cwd=worktree).stdout
        paths = self._changed_paths(status)
        self._audit_paths(paths)
        self._run(["git", "diff", "--check"], cwd=worktree)
        diff = self._run(["git", "diff", "--no-ext-diff", "--unified=0"], cwd=worktree).stdout
        self._audit_diff(diff)
        test_command = [sys.executable, "-m", "pytest", *tests, "-q"]
        test_result = self._run(test_command, cwd=worktree, timeout=1800)
        incident["test_result"] = {
            "command": "python -m pytest " + " ".join(tests) + " -q",
            "passed": True,
            "summary": sanitize_text(test_result.stdout, limit=500),
        }
        self.store.save(incident)

        self._run(["git", "add", "--", *paths], cwd=worktree)
        self._run(["git", "diff", "--cached", "--check"], cwd=worktree)
        self._run(["git", "commit", "-m", f"fix: 自动修复 {incident['error_type'].lower()}"], cwd=worktree)
        commit_sha = self._run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        incident["commit_sha"] = commit_sha
        self.store.save(incident)
        self._run(["git", "push", "-u", "origin", branch], cwd=worktree)
        pr = self._run(
            [
                "gh", "pr", "create", "--base", base, "--head", branch,
                "--title", f"fix: 自动修复 {incident['error_type']}",
                "--body", (
                    f"自动维修事件 `{incident['incident_id']}`。\n\n"
                    f"指纹：`{incident['fingerprint']}`\n\n"
                    "仅创建 PR，未合并、部署、重启或执行任何外部发送。"
                ),
            ],
            cwd=worktree,
        )
        incident["pr_url"] = pr.stdout.strip().splitlines()[-1]
        return self.store.finish(incident, success=True)
