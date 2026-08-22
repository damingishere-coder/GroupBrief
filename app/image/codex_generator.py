r"""Codex CLI ``$imagegen`` 图片生成器。

主链路使用官方 ``codex exec --json``，认证目录与图片目录统一由
``CODEX_HOME`` 派生。生成请求通过 Windows 命名互斥锁跨进程串行；只接收
本次执行后出现且能唯一归属的图片，验证后再原子替换正式文件。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.image.image_task import ImageTaskResult, detect_image_format, verify_image

logger = get_logger("groupbrief.image")

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_POLL_INTERVAL = 0.5
_MAX_POLL_ROUNDS = 20
_PROCESS_IMAGE_LOCK = threading.Lock()
_MUTEX_NAME = "Local\\GroupBrief.CodexImagegen"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80


@contextmanager
def _imagegen_mutex(timeout_seconds: float) -> Iterator[None]:
    """防止定时、手动和重生成任务跨线程/跨进程同时认领图片。"""
    if not _PROCESS_IMAGE_LOCK.acquire(timeout=max(timeout_seconds, 0.1)):
        raise TimeoutError("另一个 Codex 生图任务正在运行")
    handle = None
    owns_handle = False
    try:
        if os.name == "nt":
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            if not handle:
                raise OSError("无法创建 Codex 生图互斥锁")
            wait_code = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
            if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise TimeoutError("等待 Codex 生图互斥锁超时")
            owns_handle = True
        yield
    finally:
        if handle:
            if owns_handle:
                ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        _PROCESS_IMAGE_LOCK.release()


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

    def health_check(self) -> tuple[bool, str]:
        report = self.health_report()
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

    def generate(self, prompt_file: Path, output_path: Path) -> ImageTaskResult:
        try:
            with _imagegen_mutex(self.timeout + 30):
                return self._generate_locked(Path(prompt_file), Path(output_path))
        except TimeoutError as exc:
            return ImageTaskResult(False, error=str(exc), detail={"stage": "mutex"})
        except Exception as exc:
            logger.exception("Codex 生图互斥阶段异常")
            return ImageTaskResult(False, error=str(exc)[:300], detail={"stage": "mutex"})

    def _generate_locked(self, prompt_file: Path, output_path: Path) -> ImageTaskResult:
        ok, detail = self.health_check()
        if not ok:
            return ImageTaskResult(False, error=detail, detail={"stage": "health"})
        if not prompt_file.exists():
            return ImageTaskResult(False, error=f"image_prompt.txt 不存在：{prompt_file}", detail={"stage": "input"})

        before = self._snapshot()
        try:
            prompt_text = prompt_file.read_text(encoding="utf-8")
            command = self._build_command()
            logger.info("调用 Codex $imagegen：%s", " ".join(command)[:240])
            environment = os.environ.copy()
            environment["CODEX_HOME"] = str(self.codex_home)
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(prompt_file.parent),
                input=f"$imagegen {prompt_text}",
                env=environment,
            )
            logger.info("codex 退出码=%s", proc.returncode)
            if proc.returncode != 0:
                detail_text = (proc.stderr or proc.stdout or "")[-500:]
                return ImageTaskResult(
                    False,
                    error=f"codex 退出码 {proc.returncode}：{detail_text}",
                    detail={"stage": "exec", "outcome_unknown": False},
                )
        except FileNotFoundError as exc:
            return ImageTaskResult(False, error=f"无法启动 codex：{exc}", detail={"stage": "exec"})
        except subprocess.TimeoutExpired:
            return ImageTaskResult(
                False,
                error=f"codex 超时（>{self.timeout}s）；结果未知，禁止自动重试",
                detail={"stage": "exec", "outcome_unknown": True},
            )
        except Exception as exc:
            logger.exception("调用 codex 异常")
            return ImageTaskResult(False, error=str(exc)[:300], detail={"stage": "exec"})

        structured = self._structured_candidates(proc.stdout or "", prompt_file.parent, before)
        scanned = self._scan_new(before)
        candidates = self._unique_candidates([*structured, *scanned], before)
        if not candidates:
            return ImageTaskResult(
                False,
                error="codex 执行完成但未发现本次生成的新图片",
                detail={"stage": "save"},
            )
        if len(candidates) != 1:
            return ImageTaskResult(
                False,
                error=f"本次发现 {len(candidates)} 张新图片，无法唯一归属，已停止接管",
                detail={"stage": "ambiguous", "candidates": [str(path) for path in candidates]},
            )

        source = candidates[0]
        try:
            image_detail = self._promote_valid_image(source, output_path)
        except Exception as exc:
            return ImageTaskResult(False, error=f"图片验证或原子落盘失败：{exc}", detail={"stage": "copy"})
        # 仅项目 output 内的正式/烟测产物可更新健康标记，避免单元测试或
        # 临时目录里的模拟图片被误报为真实图片能力实测。
        if self._is_project_output(output_path):
            self._save_last_smoke(source, output_path, image_detail)
        return ImageTaskResult(
            True,
            image_path=output_path,
            detail={"source": str(source), **image_detail},
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

    def _build_command(self) -> list[str]:
        return [
            self._resolve_binary() or self.codex_path,
            "exec",
            "-C",
            ".",
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "-",
        ]

    def _snapshot(self) -> dict[str, float]:
        result: dict[str, float] = {}
        if not self.generated_images_dir.exists():
            return result
        for path in self.generated_images_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            try:
                result[str(path.resolve())] = path.stat().st_mtime
            except OSError:
                continue
        return result

    def _scan_new(self, before: dict[str, float]) -> list[Path]:
        """等待进程退出后的图片落盘，并多观察一轮以捕获多图歧义。"""
        found: list[Path] = []
        for _ in range(_MAX_POLL_ROUNDS):
            current = self._snapshot()
            found = [
                Path(path)
                for path, mtime in current.items()
                if path not in before or mtime > before[path]
            ]
            if found:
                time.sleep(_POLL_INTERVAL)
                current = self._snapshot()
                return [
                    Path(path)
                    for path, mtime in current.items()
                    if path not in before or mtime > before[path]
                ]
            time.sleep(_POLL_INTERVAL)
        return found

    def _structured_candidates(self, stdout: str, cwd: Path, before: dict[str, float]) -> list[Path]:
        candidates: list[Path] = []

        def inspect(value) -> None:
            if isinstance(value, dict):
                for item in value.values():
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
            elif isinstance(value, str):
                candidates.extend(self._paths_from_text(value, cwd))

        for line in stdout.splitlines():
            try:
                inspect(json.loads(line))
            except json.JSONDecodeError:
                continue
        return self._unique_candidates(candidates, before)

    @staticmethod
    def _paths_from_text(value: str, cwd: Path) -> list[Path]:
        raw_values = [value.strip()]
        raw_values.extend(
            match.group(0)
            for match in re.finditer(
                r"(?:[A-Za-z]:[\\/]|/)[^\r\n\"']+?\.(?:png|jpe?g|webp|gif|bmp|tiff?)",
                value,
                flags=re.IGNORECASE,
            )
        )
        paths: list[Path] = []
        for raw in raw_values:
            cleaned = raw.strip().strip("`\"'.,;:()[]{}")
            if not cleaned or Path(cleaned).suffix.lower() not in _IMAGE_EXTENSIONS:
                continue
            candidate = Path(cleaned).expanduser()
            if not candidate.is_absolute():
                candidate = cwd / candidate
            if candidate.is_file():
                paths.append(candidate.resolve())
        return paths

    @staticmethod
    def _unique_candidates(paths: list[Path], before: dict[str, float]) -> list[Path]:
        unique_paths: dict[str, Path] = {}
        for path in paths:
            try:
                resolved = path.resolve()
                stat = resolved.stat()
            except OSError:
                continue
            key = str(resolved)
            if key in before and stat.st_mtime <= before[key]:
                continue
            unique_paths[key.lower()] = resolved

        # Codex imagegen 可能把同一产物同时落到 CODEX_HOME/generated_images
        # 和当前任务目录。内容完全一致时，它们是同一张图的两个路径，不应
        # 被误判成多图；内容不同的多个候选仍必须失败关闭。
        unique_content: dict[tuple[int, str], Path] = {}
        for path in unique_paths.values():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                content_key = (path.stat().st_size, digest)
            except OSError:
                continue
            unique_content.setdefault(content_key, path)
        return sorted(unique_content.values(), key=lambda item: str(item).lower())

    @staticmethod
    def _promote_valid_image(source: Path, output_path: Path) -> dict:
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
