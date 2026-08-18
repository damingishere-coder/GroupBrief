"""Codex `$imagegen` 图片生成器（P5）。

调用方式（对应 Codex CLI 内置 `$imagegen` 工具）：
    codex exec -C <工作目录> -s workspace-write --skip-git-repo-check "\$imagegen <prompt>"

生成的 PNG 默认落在 <用户>/.codex/generated_images/<session>/ig_*.png。
本生成器在调用前记录该目录已有文件，调用后扫描新出现的图片文件并复制到目标路径，
从而不依赖解析 Codex stdout。

如果 Codex CLI 不可用，health_check 返回明确不可用状态；generate 返回失败
（IMAGE_GENERATION_FAILED），绝不把「没落盘」当作成功。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.image.image_task import (
    GeneratedImage,
    ImageTaskResult,
    copy_generated_image,
    detect_image_format,
)

logger = get_logger("groupbrief.image")

# 等待新图片出现的轮询参数
_POLL_INTERVAL = 2.0
_MAX_POLL_ROUNDS = 60  # 最长约 120 秒


class CodexImageGenerator:
    """通过 Codex CLI `$imagegen` 生成图片并落盘。"""

    name = "codex_imagegen"

    def __init__(
        self,
        settings: Settings | None = None,
        codex_path: str | None = None,
        generated_images_dir: str | None = None,
    ):
        self.settings = settings or get_settings()
        self.codex_path = codex_path or self.settings.codex_path or "codex"
        self.timeout = self.settings.codex_timeout_seconds
        self.generated_images_dir = Path(
            generated_images_dir
            or self.settings.codex_generated_images_dir
            or (Path.home() / ".codex" / "generated_images")
        )

    # ---------- 健康检查 ----------

    def health_check(self) -> tuple[bool, str]:
        resolved = shutil.which(self.codex_path)
        if resolved:
            return True, f"codex 可用：{resolved}"
        if Path(self.codex_path).exists():
            return True, f"codex 可用（显式路径）：{self.codex_path}"
        return False, (
            f"codex CLI 不可用（未找到命令：{self.codex_path}）。"
            "请安装 Codex CLI 或在设置中配置 codex_path。"
        )

    # ---------- 生成 ----------

    def generate(self, prompt_file: Path, output_path: Path) -> ImageTaskResult:
        ok, detail = self.health_check()
        if not ok:
            return ImageTaskResult(False, error=detail, detail={"stage": "health"})
        if not Path(prompt_file).exists():
            return ImageTaskResult(False, error=f"image_prompt.txt 不存在：{prompt_file}", detail={"stage": "input"})

        try:
            before = self._snapshot()
            prompt_text = Path(prompt_file).read_text(encoding="utf-8")
            command = self._build_command(prompt_text)
            logger.info("调用 Codex $imagegen：%s", " ".join(command)[:200])
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=str(Path(prompt_file).parent),
            )
            logger.info("codex 退出码=%s", proc.returncode)
            if proc.stdout:
                logger.info("codex stdout 尾部：%s", proc.stdout[-300:])
            if proc.returncode != 0:
                return ImageTaskResult(
                    False,
                    error=f"codex 退出码 {proc.returncode}：{proc.stderr[-300:] or proc.stdout[-300:]}",
                    detail={"stage": "exec"},
                )
        except FileNotFoundError as e:
            return ImageTaskResult(False, error=f"无法启动 codex：{e}", detail={"stage": "exec"})
        except subprocess.TimeoutExpired:
            return ImageTaskResult(False, error=f"codex 超时（>{self.timeout}s）", detail={"stage": "exec"})
        except Exception as e:
            logger.exception("调用 codex 异常")
            return ImageTaskResult(False, error=str(e)[:300], detail={"stage": "exec"})

        # 扫描新图片并复制
        new_files = self._scan_new(before)
        if not new_files:
            return ImageTaskResult(
                False,
                error="codex 执行完成但未发现新生成图片（可能 0.140.x 存在不落盘问题，"
                      "或 $imagegen 未触发）。",
                detail={"stage": "save"},
            )
        newest = max(new_files, key=lambda p: p.stat().st_mtime)
        try:
            copy_generated_image(newest, output_path)
        except Exception as e:
            return ImageTaskResult(False, error=f"复制图片失败：{e}", detail={"stage": "copy"})
        return ImageTaskResult(
            True,
            image_path=output_path,
            detail={
                "source": str(newest),
                "format": detect_image_format(output_path),
                "size_bytes": output_path.stat().st_size,
            },
        )

    # ---------- 内部 ----------

    def _build_command(self, prompt_text: str) -> list[str]:
        # 传给 codex exec 的单行 prompt：触发内置 $imagegen
        instruction = f"$imagegen {prompt_text}"
        return [
            self.codex_path,
            "exec",
            "-C",
            ".",
            "-s",
            "workspace-write",
            "--skip-git-repo-check",
            instruction,
        ]

    def _snapshot(self) -> dict[str, float]:
        """记录 generated_images 目录下现有文件的 {path: mtime}。"""
        result: dict[str, float] = {}
        if not self.generated_images_dir.exists():
            return result
        for p in self.generated_images_dir.rglob("*.png"):
            try:
                result[str(p)] = p.stat().st_mtime
            except OSError:
                continue
        return result

    def _scan_new(self, before: dict[str, float]) -> list[Path]:
        """轮询等待新图片出现（最多 _MAX_POLL_ROUNDS 轮）。"""
        for _ in range(_MAX_POLL_ROUNDS):
            time.sleep(_POLL_INTERVAL)
            current = self._snapshot()
            new_files = [
                Path(path)
                for path in current
                if path not in before or current[path] > before[path]
            ]
            if new_files:
                return new_files
        return []
