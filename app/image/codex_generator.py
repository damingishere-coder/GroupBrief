r"""Codex CLI ``$imagegen`` 图片生成器。

主链路使用官方 ``codex exec --json``，认证目录与图片目录统一由
``CODEX_HOME`` 派生。生成请求通过进程级与 Windows 命名信号量受控并发；
自动流程只接收带匹配 job_id 的结构化回执，其他候选仅供人工恢复。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from app.config.settings import Settings, get_settings
from app.ai.concurrency import bounded_slot, normalized_limit
from app.core.logging import get_logger
from app.image.image_task import ImageTaskResult, detect_image_format, verify_image

logger = get_logger("groupbrief.image")

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_POLL_INTERVAL = 0.5
_RECOVERY_POLL_ROUNDS = 30
_MAX_ATTEMPTS = 2
_ATTEMPT_MANIFEST = "attempt.json"
_JOB_DIR = ".imagegen-jobs"
_RESULT_SCHEMA = Path(__file__).with_name("codex_image_result.schema.json")
_PROCESS_IMAGE_LOCK = threading.Lock()  # 兼容旧测试/扩展，不再作为全局单槽锁。
_MUTEX_NAME = "Local\\GroupBrief.CodexImagegen.v2"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def re_fullmatch_job_id(value: str) -> bool:
    return bool(_JOB_ID_RE.fullmatch(value or ""))


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """终止 Codex 整棵进程树，避免 Windows 外层 cmd 超时后孙进程占住管道。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _run_codex_process(
    command: list[str],
    *,
    timeout: int,
    cwd: str,
    input: str,
    env: dict[str, str],
    on_start: Callable[[int], None] | None = None,
) -> subprocess.CompletedProcess:
    """运行 Codex；超时时先杀进程树，再回收管道并抛出 TimeoutExpired。"""
    popen_kwargs: dict = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "env": env,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    if on_start is not None:
        try:
            on_start(process.pid)
        except Exception:
            _terminate_process_tree(process)
            try:
                process.communicate(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
            raise
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        try:
            process.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
            except OSError:
                pass
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


@contextmanager
def _imagegen_mutex(timeout_seconds: float, limit: int = 1) -> Iterator[None]:
    """兼容旧函数名的跨进程受控并发槽。"""
    limit = normalized_limit(limit, 1, maximum=6)
    handle = None
    owns_handle = False
    with bounded_slot("codex_image_request", limit):
        try:
            if os.name == "nt":
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CreateSemaphoreW.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_long,
                    ctypes.c_long,
                    ctypes.c_wchar_p,
                ]
                kernel32.CreateSemaphoreW.restype = ctypes.c_void_p
                kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                kernel32.WaitForSingleObject.restype = ctypes.c_uint
                kernel32.ReleaseSemaphore.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_long,
                    ctypes.c_void_p,
                ]
                kernel32.ReleaseSemaphore.restype = ctypes.c_int
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_int
                handle = kernel32.CreateSemaphoreW(
                    None, limit, limit, _MUTEX_NAME
                )
                if not handle:
                    raise OSError("无法创建 Codex 生图并发信号量")
                wait_code = kernel32.WaitForSingleObject(
                    handle, int(timeout_seconds * 1000)
                )
                if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                    raise TimeoutError("等待 Codex 生图并发槽超时")
                owns_handle = True
            yield
        finally:
            if handle:
                if owns_handle:
                    kernel32.ReleaseSemaphore(handle, 1, None)
                kernel32.CloseHandle(handle)


class CodexImageGenerator:
    """通过 Codex CLI ``$imagegen`` 生成一张图片并安全落盘。"""

    name = "codex_imagegen"

    def __init__(
        self,
        settings: Settings | None = None,
        codex_path: str | None = None,
        generated_images_dir: str | None = None,
    ):
        self.settings = settings or get_settings()
        self.codex_path = codex_path or self.settings.codex_path or "codex"
        self.timeout = int(self.settings.codex_timeout_seconds)

        configured_home = (
            self.settings.codex_home
            or os.environ.get("CODEX_HOME", "")
            or str(Path.home() / ".codex")
        )
        self.codex_home = Path(configured_home).expanduser().resolve()
        if generated_images_dir or self.settings.codex_generated_images_dir:
            self.generated_images_dir = Path(
                generated_images_dir or self.settings.codex_generated_images_dir
            ).expanduser().resolve()
            if not self.settings.codex_home and not os.environ.get("CODEX_HOME"):
                if self.generated_images_dir.name.lower() == "generated_images":
                    self.codex_home = self.generated_images_dir.parent
        else:
            self.generated_images_dir = self.codex_home / "generated_images"

        self._resolved_binary = ""

    # ---------- 健康检查 ----------

    def health_report(self) -> dict:
        resolved = self._resolve_binary()
        binary = {
            "ok": bool(resolved),
            "configured": self.codex_path,
            "resolved": resolved,
        }
        version = {"ok": False, "value": "", "detail": "尚未执行"}
        if resolved:
            try:
                proc = subprocess.run(
                    [resolved, "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    cwd=str(self.settings.output_dir.parent),
                )
                output = (proc.stdout or proc.stderr or "").strip().splitlines()
                value = output[-1][:160] if output else ""
                version = {
                    "ok": proc.returncode == 0 and bool(value),
                    "value": value,
                    "detail": "codex --version 执行成功" if proc.returncode == 0 else f"退出码 {proc.returncode}",
                }
            except Exception as exc:
                version = {"ok": False, "value": "", "detail": f"无法执行 codex --version：{str(exc)[:180]}"}

        smoke = self._load_last_smoke()
        return {
            "ok": bool(binary["ok"] and version["ok"]),
            "binary": binary,
            "version": version,
            "last_image_smoke": smoke,
            "codex_home": str(self.codex_home),
            "generated_images_dir": str(self.generated_images_dir),
        }

    def health_check(self, report: dict | None = None) -> tuple[bool, str]:
        report = report or self.health_report()
        if not report["binary"]["ok"]:
            return False, (
                f"codex CLI 不可用（未找到命令：{self.codex_path}）。"
                "请安装 Codex CLI 或在设置中配置 codex_path。"
            )
        if not report["version"]["ok"]:
            return False, report["version"]["detail"]
        smoke = report["last_image_smoke"]
        smoke_text = (
            f"最近图片实测：{smoke.get('completed_at')}"
            if smoke.get("ok")
            else "图片能力尚未实测"
        )
        return True, f"codex 可执行：{report['version']['value']}；{smoke_text}"

    # ---------- 生成 ----------

    def generate(
        self,
        prompt_file: Path,
        output_path: Path,
        *,
        force: bool = False,
        job_id: str = "",
        revision: int = 1,
        prompt_sha256: str = "",
    ) -> ImageTaskResult:
        # 配置/可执行性错误不应排队等待全局生图锁；进入锁后仍会再次检查，
        # 以覆盖等待期间 CLI 状态发生变化的情况。
        ok, detail = self.health_check()
        if not ok:
            return ImageTaskResult(False, error=detail, detail={"stage": "health"})
        try:
            with _imagegen_mutex(
                (self.timeout * _MAX_ATTEMPTS) + 60,
                normalized_limit(self.settings.image_generation_concurrency, 2, maximum=6),
            ):
                return self._generate_locked(
                    Path(prompt_file),
                    Path(output_path),
                    force=force,
                    job_id=job_id,
                    revision=revision,
                    prompt_sha256=prompt_sha256,
                )
        except TimeoutError as exc:
            return ImageTaskResult(False, error=str(exc), detail={"stage": "mutex"})
        except Exception as exc:
            logger.exception("Codex 生图互斥阶段异常")
            return ImageTaskResult(False, error=str(exc)[:300], detail={"stage": "mutex"})

    def _generate_locked(
        self,
        prompt_file: Path,
        output_path: Path,
        *,
        force: bool = False,
        job_id: str = "",
        revision: int = 1,
        prompt_sha256: str = "",
    ) -> ImageTaskResult:
        ok, detail = self.health_check()
        if not ok:
            return ImageTaskResult(False, error=detail, detail={"stage": "health"})
        if not prompt_file.exists():
            return ImageTaskResult(False, error=f"image_prompt.txt 不存在：{prompt_file}", detail={"stage": "input"})

        # ImageJob 的锁外检查只能优化单进程路径；跨进程排队后必须在同一
        # 生图 mutex 内重新确认，避免两个执行者依次重复生成同一张图。
        if not force:
            existing_ok, _ = verify_image(output_path)
            if existing_ok:
                return ImageTaskResult(
                    True,
                    image_path=output_path,
                    detail={
                        "stage": "existing",
                        "recovery_status": "existing_output_reused",
                        "attempt_count": 0,
                    },
                )

        try:
            prompt_text = prompt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return ImageTaskResult(False, error=f"无法读取 image_prompt.txt：{exc}", detail={"stage": "input"})
        required_size = (
            (1024, 1536)
            if "1024×1536" in prompt_text or "1024x1536" in prompt_text.lower()
            else None
        )

        # 任务哈希按磁盘原始字节创建；Windows 的 read_text 会把 CRLF 规范化
        # 为 LF，不能再对解码后的文本计算哈希，否则内容未变也会被误判。
        actual_prompt_sha256 = hashlib.sha256(prompt_file.read_bytes()).hexdigest()
        if prompt_sha256 and prompt_sha256.lower() != actual_prompt_sha256:
            return ImageTaskResult(
                False,
                error="Prompt 内容已变化，拒绝使用旧生图任务",
                detail={"stage": "input", "prompt_sha256": actual_prompt_sha256},
            )
        job_id = (job_id or uuid.uuid4().hex).strip()
        if not re_fullmatch_job_id(job_id):
            return ImageTaskResult(False, error="生图 job_id 格式无效", detail={"stage": "input"})

        task_dir = prompt_file.parent.resolve()
        output_path = output_path.resolve()
        job_dir = task_dir / _JOB_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = job_dir / _ATTEMPT_MANIFEST
        history: list[dict] = []
        next_attempt = 1

        previous = self._load_attempt_manifest(manifest_path)
        if previous:
            history = list(previous.get("attempt_history") or [])
            previous_number = self._safe_attempt_number(previous.get("attempt_number"))
            if previous.get("state") == "completed" and not force:
                return ImageTaskResult(
                    False,
                    error="上次生图已确认完成但正式输出缺失，禁止自动重复生成",
                    detail={
                        "stage": "resume",
                        "outcome_unknown": False,
                        "attempt_count": previous_number,
                        "recovery_status": "completed_output_missing",
                        "candidate_diagnostics": previous.get("candidate_diagnostics") or [],
                        "attempts": history,
                    },
                )
            if previous.get("state") == "exhausted":
                return self._attempts_exhausted_result(previous_number, history, previous)
            if previous.get("state") == "blocked_process":
                return ImageTaskResult(
                    False,
                    error="上次 Codex 生图进程仍未确认结束，禁止启动新尝试",
                    detail={
                        "stage": "resume",
                        "outcome_unknown": True,
                        "attempt_count": previous_number,
                        "recovery_status": "timeout_process_still_running",
                        "candidate_diagnostics": previous.get("candidate_diagnostics") or [],
                        "attempts": history,
                    },
                )
            if previous.get("state") == "result_unknown":
                return ImageTaskResult(
                    False,
                    error="上次 Codex 生图已启动但结果未知，禁止自动重复生成",
                    detail={
                        "stage": "resume",
                        "outcome_unknown": True,
                        "attempt_count": previous_number,
                        "recovery_status": "result_unknown_hold",
                        "candidate_diagnostics": previous.get("candidate_diagnostics") or [],
                        "attempts": history,
                    },
                )
            if previous.get("state") == "retrying":
                if previous.get("outcome") == "start_failed":
                    next_attempt = previous_number + 1
                else:
                    previous.update(
                        state="result_unknown",
                        recovery_reason="旧重试记录无法证明外部调用未发生，已转人工复核",
                    )
                    self._write_attempt_manifest(manifest_path, previous)
                    return ImageTaskResult(
                        False,
                        error="旧 Codex 生图重试记录结果未知，禁止自动重复生成",
                        detail={
                            "stage": "resume",
                            "outcome_unknown": True,
                            "attempt_count": previous_number,
                            "recovery_status": "legacy_retry_result_unknown",
                            "candidate_diagnostics": previous.get("candidate_diagnostics") or [],
                            "attempts": history,
                        },
                    )
            if previous.get("state") == "running":
                source, diagnostics, reason = self._reconcile_attempt(previous, task_dir)
                recovered_history = [
                    *history,
                    self._attempt_audit(previous, "interrupted", reason, diagnostics),
                ]
                recovered = self._promote_recovered_candidate(
                    source,
                    output_path,
                    recovery_status="recovered_after_interruption",
                    attempt_number=previous_number,
                    diagnostics=diagnostics,
                    attempts=recovered_history,
                    manifest_path=manifest_path,
                    attempt=previous,
                    required_size=required_size,
                )
                if recovered is not None:
                    return recovered
                if not self._ensure_recorded_process_stopped(previous):
                    return ImageTaskResult(
                        False,
                        error="上次 Codex 生图进程仍未确认结束，已停止自动重试",
                        detail={
                            "stage": "resume",
                            "outcome_unknown": True,
                            "attempt_count": previous_number,
                            "recovery_status": "interrupted_process_still_running",
                            "candidate_diagnostics": diagnostics,
                            "attempts": history,
                        },
                    )
                previous.update(
                    state="result_unknown",
                    finished_at=datetime_now_iso(),
                    outcome="interrupted",
                    recovery_reason="进程已停止但没有可信候选，外部调用结果未知",
                    attempt_history=recovered_history,
                    candidate_diagnostics=diagnostics,
                )
                self._write_attempt_manifest(manifest_path, previous)
                return ImageTaskResult(
                    False,
                    error="上次 Codex 生图进程已停止但结果未知，禁止自动重试",
                    detail={
                        "stage": "resume",
                        "outcome_unknown": True,
                        "attempt_count": previous_number,
                        "recovery_status": "interrupted_result_unknown",
                        "candidate_diagnostics": diagnostics,
                        "attempts": recovered_history,
                    },
                )

        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.codex_home)
        last_reason = ""
        last_diagnostics: list[dict] = []

        for attempt_number in range(next_attempt, _MAX_ATTEMPTS + 1):
            attempt = self._new_attempt(
                task_dir,
                attempt_number,
                history,
                job_id=job_id,
                revision=revision,
                prompt_sha256=actual_prompt_sha256,
                job_dir=job_dir,
            )
            self._write_attempt_manifest(manifest_path, attempt)
            command = self._build_command(Path(attempt["result_path"]))
            logger.info(
                "调用 Codex $imagegen：attempt=%d/%d id=%s",
                attempt_number,
                _MAX_ATTEMPTS,
                attempt["attempt_id"],
            )
            outcome = "completed"
            exit_code: int | None = None
            try:
                proc = _run_codex_process(
                    command,
                    timeout=self.timeout,
                    cwd=str(task_dir),
                    input=self._attempt_prompt(prompt_text, job_id),
                    env=environment,
                    on_start=lambda pid, record=attempt: self._record_attempt_pid(
                        manifest_path, record, pid
                    ),
                )
                exit_code = int(proc.returncode)
                if proc.returncode != 0:
                    outcome = "nonzero_exit"
            except FileNotFoundError:
                outcome = "start_failed"
            except subprocess.TimeoutExpired:
                outcome = "timeout"
            except Exception:
                logger.exception("调用 codex 异常")
                outcome = "exec_error"

            source, diagnostics, reason = self._reconcile_attempt(attempt, task_dir)
            last_reason = reason
            last_diagnostics = diagnostics
            recovery_status = {
                "timeout": "recovered_after_timeout",
                "nonzero_exit": "recovered_after_nonzero_exit",
                "completed": "completed",
            }.get(outcome, "recovered_after_exec_error")
            recovered = self._promote_recovered_candidate(
                source,
                output_path,
                recovery_status=recovery_status,
                attempt_number=attempt_number,
                diagnostics=diagnostics,
                attempts=history,
                manifest_path=manifest_path,
                attempt=attempt,
                required_size=required_size,
            )
            audit = self._attempt_audit(attempt, outcome, reason, diagnostics, exit_code)
            history.append(audit)
            if recovered is not None:
                recovered.detail["attempts"] = history
                return recovered
            if source is not None:
                reason = "唯一候选未通过完整图片验证"
                last_reason = reason
                audit["recovery_reason"] = reason
                attempt.update(
                    state="retrying" if attempt_number < _MAX_ATTEMPTS else "exhausted",
                    finished_at=datetime_now_iso(),
                    outcome="invalid_output",
                    exit_code=exit_code,
                    recovery_reason=reason,
                    attempt_history=history,
                    candidate_diagnostics=diagnostics,
                )
                self._write_attempt_manifest(manifest_path, attempt)
                self._cleanup_attempt_files(attempt)
                continue

            if outcome == "timeout" and not self._ensure_recorded_process_stopped(attempt):
                reason = "超时进程树未确认结束，禁止启动重试"
                audit["recovery_reason"] = reason
                attempt.update(
                    state="blocked_process",
                    finished_at=datetime_now_iso(),
                    outcome=outcome,
                    recovery_reason=reason,
                    attempt_history=history,
                    candidate_diagnostics=diagnostics,
                )
                self._write_attempt_manifest(manifest_path, attempt)
                return ImageTaskResult(
                    False,
                    error="Codex 超时进程树未确认结束，已禁止自动重试",
                    detail={
                        "stage": "exec",
                        "outcome_unknown": True,
                        "attempt_count": attempt_number,
                        "recovery_status": "timeout_process_still_running",
                        "candidate_diagnostics": diagnostics,
                        "attempts": history,
                    },
                )

            if outcome != "start_failed":
                reason = reason or "外部生图已启动但没有可信结果"
                audit["recovery_reason"] = reason
                attempt.update(
                    state="result_unknown",
                    finished_at=datetime_now_iso(),
                    outcome=outcome,
                    exit_code=exit_code,
                    recovery_reason=reason,
                    attempt_history=history,
                    candidate_diagnostics=diagnostics,
                )
                self._write_attempt_manifest(manifest_path, attempt)
                return ImageTaskResult(
                    False,
                    error="Codex 生图已启动但结果未知，已禁止自动重试",
                    detail={
                        "stage": "ambiguous" if len(diagnostics) > 1 else "exec",
                        "outcome_unknown": True,
                        "attempt_count": attempt_number,
                        "recovery_status": "result_unknown_hold",
                        "candidate_diagnostics": diagnostics,
                        "attempts": history,
                    },
                )

            attempt.update(
                state="retrying" if attempt_number < _MAX_ATTEMPTS else "exhausted",
                finished_at=datetime_now_iso(),
                outcome=outcome,
                exit_code=exit_code,
                recovery_reason=reason,
                attempt_history=history,
                candidate_diagnostics=diagnostics,
            )
            self._write_attempt_manifest(manifest_path, attempt)
            self._cleanup_attempt_files(attempt)
            if attempt_number < _MAX_ATTEMPTS:
                logger.warning(
                    "Codex 生图第 %d 次尝试未获得唯一图片，将进行最后一次重试：%s",
                    attempt_number,
                    reason,
                )

        error = (
            f"Codex 生图两次尝试后仍无法认领唯一图片：{last_reason}"
            if last_reason
            else "Codex 生图两次尝试后仍失败"
        )
        return ImageTaskResult(
            False,
            error=error,
            detail={
                "stage": "ambiguous" if len(last_diagnostics) > 1 else "save",
                "outcome_unknown": False,
                "attempt_count": min(max(len(history), 1), _MAX_ATTEMPTS),
                "recovery_status": "retry_exhausted",
                "candidate_diagnostics": last_diagnostics,
                "attempts": history,
            },
        )

    # ---------- 内部 ----------

    def _resolve_binary(self) -> str:
        if self._resolved_binary:
            return self._resolved_binary
        resolved = shutil.which(self.codex_path)
        if not resolved:
            candidate = Path(self.codex_path).expanduser()
            if candidate.is_file():
                resolved = str(candidate.resolve())
        self._resolved_binary = resolved or ""
        return self._resolved_binary

    def _build_command(self, result_path: Path) -> list[str]:
        return [
            self._resolve_binary() or self.codex_path,
            "exec",
            "-C",
            ".",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--ephemeral",
            "--output-schema",
            str(_RESULT_SCHEMA.resolve()),
            "--output-last-message",
            str(result_path.resolve()),
            "--json",
            "-",
        ]

    @staticmethod
    def _safe_attempt_number(value: object) -> int:
        try:
            return min(max(int(value), 1), _MAX_ATTEMPTS)
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _attempt_prompt(prompt_text: str, job_id: str) -> str:
        return (
            "$imagegen " + prompt_text + "\n\n"
            f"本次生图任务 ID 是 {job_id}。"
            "只为本次任务调用一次 ImageGen，并只生成、选择一张最终图片。"
            "最终图片的原始画布必须精确为 1024×1536 像素、严格 2:3 比例；"
            "不得选择 9:19 超长手机截图比例或 864×1821 画布，不得依靠裁切补救。"
            "不要读取、引用或复用任何已有图片，也不要复制或另存生成结果。"
            "最终回复必须严格符合 output schema，在 job_id 中逐字返回本次任务 ID，并在 image_path 中返回 "
            "ImageGen 产生的这张最终图片当前存在的绝对路径。"
        )

    def _new_attempt(
        self,
        task_dir: Path,
        attempt_number: int,
        history: list[dict],
        *,
        job_id: str = "",
        revision: int = 1,
        prompt_sha256: str = "",
        job_dir: Path | None = None,
    ) -> dict:
        attempt_id = uuid.uuid4().hex
        job_id = job_id or uuid.uuid4().hex
        job_dir = job_dir or (task_dir / _JOB_DIR / job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return {
            "version": 2,
            "state": "running",
            "job_id": job_id,
            "revision": max(1, int(revision)),
            "prompt_sha256": prompt_sha256,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
            "started_at": datetime_now_iso(),
            "pid": None,
            "staging_path": str(job_dir / f"candidate-{attempt_id}.png"),
            "result_path": str(job_dir / f"receipt-{attempt_id}.json"),
            "before": self._snapshot(task_dir),
            "attempt_history": list(history),
        }

    @staticmethod
    def _write_attempt_manifest(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _load_attempt_manifest(path: Path) -> dict | None:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and parsed.get("attempt_id"):
                return parsed
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _record_attempt_pid(self, manifest_path: Path, attempt: dict, pid: int) -> None:
        attempt["pid"] = int(pid)
        self._write_attempt_manifest(manifest_path, attempt)

    def _snapshot(self, task_dir: Path) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        roots = (("task", task_dir.resolve()), ("generated_images", self.generated_images_dir.resolve()))
        for label, root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTENSIONS:
                    continue
                try:
                    relative = path.resolve().relative_to(root).as_posix()
                    stat = path.stat()
                except (OSError, ValueError):
                    continue
                result[f"{label}:{relative}"] = {
                    "mtime_ns": int(stat.st_mtime_ns),
                    "size": int(stat.st_size),
                }
        return result

    def _path_label(self, path: Path, task_dir: Path) -> tuple[str, str] | None:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        for label, root in (
            ("task", task_dir.resolve()),
            ("generated_images", self.generated_images_dir.resolve()),
        ):
            try:
                return label, resolved.relative_to(root).as_posix()
            except ValueError:
                continue
        return None

    def _is_new_candidate(self, path: Path, task_dir: Path, before: dict) -> bool:
        label = self._path_label(path, task_dir)
        if label is None or path.suffix.lower() not in _IMAGE_EXTENSIONS:
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        old = before.get(f"{label[0]}:{label[1]}")
        return not isinstance(old, dict) or (
            int(old.get("mtime_ns") or 0) != stat.st_mtime_ns
            or int(old.get("size") or -1) != stat.st_size
        )

    @staticmethod
    def _structured_result_path(result_path: Path, expected_job_id: str) -> Path | None:
        try:
            parsed = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(parsed, dict) or set(parsed) != {"job_id", "image_path"}:
            return None
        if str(parsed.get("job_id") or "") != expected_job_id:
            return None
        raw = parsed.get("image_path")
        if not isinstance(raw, str) or not raw.strip():
            return None
        candidate = Path(raw.strip()).expanduser()
        if not candidate.is_absolute():
            return None
        return candidate

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().upper()

    def _candidate_records(self, attempt: dict, task_dir: Path) -> list[tuple[Path, dict]]:
        before = attempt.get("before") if isinstance(attempt.get("before"), dict) else {}
        staging_path = Path(str(attempt.get("staging_path") or ""))
        result_path = Path(str(attempt.get("result_path") or ""))
        paths: list[tuple[Path, str]] = []
        if self._is_new_candidate(staging_path, task_dir, before):
            paths.append((staging_path, "staging"))
        structured = self._structured_result_path(
            result_path, str(attempt.get("job_id") or "")
        )
        if structured is not None and self._is_new_candidate(structured, task_dir, before):
            paths.append((structured, "structured"))
        current = self._snapshot(task_dir)
        for key, meta in current.items():
            old = before.get(key)
            if isinstance(old, dict) and old == meta:
                continue
            label, relative = key.split(":", 1)
            root = task_dir if label == "task" else self.generated_images_dir
            paths.append((root / Path(relative), "scan"))

        by_hash: dict[tuple[int, str], tuple[Path, dict]] = {}
        priority = {"staging": 0, "structured": 1, "scan": 2}
        for path, source in paths:
            label = self._path_label(path, task_dir)
            if label is None:
                continue
            try:
                stat = path.stat()
                digest = self._sha256(path)
            except OSError:
                continue
            key = (stat.st_size, digest)
            record = {
                "source": source,
                "sources": [source],
                "root": label[0],
                "relative_path": label[1],
                "mtime_ns": int(stat.st_mtime_ns),
                "size_bytes": int(stat.st_size),
                "sha256": digest,
            }
            existing = by_hash.get(key)
            if existing is None:
                by_hash[key] = (path.resolve(), record)
                continue
            existing_path, existing_record = existing
            sources = set(existing_record.get("sources") or [])
            sources.add(source)
            existing_record["sources"] = sorted(sources, key=lambda item: priority[item])
            if priority[source] < priority[str(existing_record.get("source") or "scan")]:
                record["sources"] = existing_record["sources"]
                by_hash[key] = (path.resolve(), record)
            else:
                by_hash[key] = (existing_path, existing_record)
        return sorted(by_hash.values(), key=lambda item: (priority[item[1]["source"]], item[1]["relative_path"]))

    @staticmethod
    def _select_candidate(records: list[tuple[Path, dict]]) -> tuple[Path | None, str]:
        if not records:
            return None, "未发现本次尝试新增或修改的有效候选"
        structured = [item for item in records if "structured" in item[1].get("sources", [])]
        if len(structured) == 1:
            return structured[0][0], "job_id 匹配的结构化最终路径唯一"
        return None, f"发现 {len(records)} 个候选但缺少匹配 job_id 的可信回执，禁止自动猜图"

    def _reconcile_attempt(self, attempt: dict, task_dir: Path) -> tuple[Path | None, list[dict], str]:
        last_records: list[tuple[Path, dict]] = []
        last_reason = ""
        for round_number in range(_RECOVERY_POLL_ROUNDS + 1):
            last_records = self._candidate_records(attempt, task_dir)
            selected, last_reason = self._select_candidate(last_records)
            if selected is not None:
                return selected, [record for _, record in last_records], last_reason
            if round_number < _RECOVERY_POLL_ROUNDS:
                time.sleep(_POLL_INTERVAL)
        return None, [record for _, record in last_records], last_reason

    @staticmethod
    def _attempt_audit(
        attempt: dict,
        outcome: str,
        reason: str,
        diagnostics: list[dict],
        exit_code: int | None = None,
    ) -> dict:
        return {
            "job_id": str(attempt.get("job_id") or ""),
            "attempt_id": str(attempt.get("attempt_id") or ""),
            "attempt_number": int(attempt.get("attempt_number") or 1),
            "started_at": str(attempt.get("started_at") or ""),
            "finished_at": datetime_now_iso(),
            "outcome": outcome,
            "exit_code": exit_code,
            "recovery_reason": reason,
            "candidate_count": len(diagnostics),
        }

    def _promote_recovered_candidate(
        self,
        source: Path | None,
        output_path: Path,
        *,
        recovery_status: str,
        attempt_number: int,
        diagnostics: list[dict],
        attempts: list[dict],
        manifest_path: Path,
        attempt: dict,
        required_size: tuple[int, int] | None = None,
    ) -> ImageTaskResult | None:
        if source is None:
            return None
        try:
            image_detail = self._promote_valid_image(
                source,
                output_path,
                required_size=required_size,
            )
        except Exception:
            logger.warning("候选图片验证失败，将按有限重试策略继续", exc_info=True)
            return None
        if self._is_project_output(output_path):
            self._save_last_smoke(source, output_path, image_detail)
        self._cleanup_attempt_files(attempt)
        manifest_path.unlink(missing_ok=True)
        return ImageTaskResult(
            True,
            image_path=output_path,
            detail={
                "job_id": str(attempt.get("job_id") or ""),
                "revision": int(attempt.get("revision") or 1),
                "prompt_sha256": str(attempt.get("prompt_sha256") or ""),
                "attempt_count": attempt_number,
                "recovery_status": recovery_status,
                "candidate_diagnostics": diagnostics,
                "attempts": list(attempts),
                **image_detail,
            },
        )

    @staticmethod
    def _cleanup_attempt_files(attempt: dict) -> None:
        for key in ("staging_path", "result_path"):
            raw = str(attempt.get(key) or "")
            if raw:
                try:
                    Path(raw).unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x100000, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 0x102
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _ensure_recorded_process_stopped(self, attempt: dict) -> bool:
        try:
            pid = int(attempt.get("pid") or 0)
        except (TypeError, ValueError):
            return True
        if not self._pid_is_running(pid):
            return True
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError):
                return False
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        for _ in range(10):
            if not self._pid_is_running(pid):
                return True
            time.sleep(0.25)
        return False

    @staticmethod
    def _attempts_exhausted_result(attempt_number: int, history: list[dict], attempt: dict) -> ImageTaskResult:
        diagnostics = attempt.get("candidate_diagnostics")
        if not isinstance(diagnostics, list):
            diagnostics = []
        return ImageTaskResult(
            False,
            error="本任务的 Codex 生图尝试已耗尽；禁止无限自动重试",
            detail={
                "stage": "save",
                "outcome_unknown": False,
                "attempt_count": attempt_number,
                "recovery_status": "retry_exhausted",
                "candidate_diagnostics": diagnostics,
                "attempts": history,
            },
        )

    @staticmethod
    def _promote_valid_image(
        source: Path,
        output_path: Path,
        *,
        required_size: tuple[int, int] | None = None,
    ) -> dict:
        from PIL import Image

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        width = 0
        height = 0
        try:
            with Image.open(source) as image:
                image.load()
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError(f"图片尺寸无效：{width}x{height}")
                if required_size is not None and (width, height) != required_size:
                    raise ValueError(
                        f"图片尺寸必须为 {required_size[0]}×{required_size[1]}，"
                        f"实际为 {width}×{height}；禁止裁切补救"
                    )
                image.convert("RGB").save(temp_path, format="PNG")
            ok, detail = verify_image(temp_path)
            if not ok:
                raise ValueError(detail)
            os.replace(temp_path, output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return {
            "format": detect_image_format(output_path),
            "size_bytes": output_path.stat().st_size,
            "sha256": CodexImageGenerator._sha256(output_path),
            "width": width,
            "height": height,
        }

    @property
    def _smoke_path(self) -> Path:
        return self.settings.output_dir / ".health" / "codex_image_smoke.json"

    def _is_project_output(self, output_path: Path) -> bool:
        try:
            output_path.resolve().relative_to(self.settings.output_dir.resolve())
            return True
        except ValueError:
            return False

    def _load_last_smoke(self) -> dict:
        try:
            parsed = json.loads(self._smoke_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return {"ok": True, **parsed}
        except (OSError, json.JSONDecodeError):
            pass
        return {"ok": False, "status": "NOT_RUN", "detail": "尚未完成真实图片生成"}

    def _save_last_smoke(self, source: Path, output_path: Path, detail: dict) -> None:
        path = self._smoke_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "OK",
            "completed_at": datetime_now_iso(),
            "source": str(source),
            "output": str(output_path.resolve()),
            **detail,
        }
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


def datetime_now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()
