"""日志查看 API（仅本机）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.config.settings import get_settings

router = APIRouter(prefix="/api/logs", tags=["logs"])

LOG_FILES = [
    "app.log",
    "provider.log",
    "ai.log",
    "scheduler.log",
    "email.log",
]


@router.get("/files")
def list_log_files():
    settings = get_settings()
    result = []
    for name in LOG_FILES:
        path: Path = settings.logs_dir / name
        size = path.stat().st_size if path.exists() else 0
        result.append({"name": name, "size": size})
    return result


@router.get("/{filename}")
def read_log(filename: str, tail: int = 200):
    settings = get_settings()
    if filename not in LOG_FILES:
        return PlainTextResponse("not found", status_code=404)
    path: Path = settings.logs_dir / filename
    if not path.exists():
        return PlainTextResponse("（日志文件尚未生成）")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return PlainTextResponse("\n".join(lines[-tail:]))
