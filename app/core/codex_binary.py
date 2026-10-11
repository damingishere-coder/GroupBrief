"""在桌面 Codex 更新移除旧版本后，重新定位同一安装目录内的 CLI。"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


def resolve_codex_binary(configured: str) -> str:
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())

    # 只恢复已明确配置的桌面版路径；拼写错误、自定义安装及普通命令
    # 不得静默切换到另一个 CLI（PATH 上可能仍是较旧的 npm 版本）。
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data or not candidate.is_absolute():
        return ""
    install_root = (Path(local_app_data) / "OpenAI" / "Codex" / "bin").resolve()
    candidate = candidate.resolve()
    if (
        candidate.name.lower() != "codex.exe"
        or candidate.parent.parent != install_root
        or not re.fullmatch(r"[a-fA-F0-9]{16}", candidate.parent.name)
    ):
        return ""

    available: list[tuple[int, str]] = []
    try:
        for binary in install_root.glob("*/codex.exe"):
            actual = binary.resolve()
            if (
                actual.parent.parent == install_root
                and re.fullmatch(r"[a-fA-F0-9]{16}", actual.parent.name)
                and actual.is_file()
            ):
                available.append((actual.stat().st_mtime_ns, str(actual)))
    except OSError:
        return ""
    # 不选 bin 根目录下可能滞后的旧启动器。执行能力由既有健康检查验证。
    return max(available)[1] if available else ""
