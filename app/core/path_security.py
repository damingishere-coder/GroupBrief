"""本地文件路径安全边界。

所有来自 API、数据库显示名或历史文件的路径片段，在进入 output 目录前
都必须经过这里的导航检查和真实路径 containment 校验。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PATH_SPLIT = re.compile(r"[\\/]+")


class PathBoundaryError(ValueError):
    """用户输入可能逃出预期文件根目录。"""


def validate_iso_date(value: str, *, field_name: str = "date") -> str:
    """只接受真实存在的 YYYY-MM-DD 日期。"""
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise ValueError(f"{field_name} 必须是有效的 YYYY-MM-DD 日期")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是有效的 YYYY-MM-DD 日期") from exc
    return value


def validate_path_label(value: str, *, field_name: str = "name") -> str:
    """允许普通显示名中的标点，但拒绝路径导航、盘符和绝对路径。"""
    if not isinstance(value, str) or "\x00" in value:
        raise PathBoundaryError(f"{field_name} 包含不安全的路径内容")
    if PurePosixPath(value).is_absolute():
        raise PathBoundaryError(f"{field_name} 不能是绝对路径")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive:
        raise PathBoundaryError(f"{field_name} 不能包含盘符或 UNC 路径")
    if any(part in {".", ".."} for part in _PATH_SPLIT.split(value)):
        raise PathBoundaryError(f"{field_name} 不能包含路径导航段")
    return value


def resolve_within(root: Path | str, *parts: Path | str, allow_root: bool = False) -> Path:
    """解析路径并证明它位于 root 内；同时阻断 symlink 和 sibling-prefix 绕过。"""
    try:
        resolved_root = Path(root).resolve()
        candidate = resolved_root.joinpath(*(Path(part) for part in parts)).resolve()
        candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathBoundaryError("路径超出允许的文件目录") from exc
    if not allow_root and candidate == resolved_root:
        raise PathBoundaryError("路径不能指向文件根目录")
    return candidate
