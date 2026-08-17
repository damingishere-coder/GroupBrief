"""GroupBrief 日志配置。

分类日志文件：
- logs/app.log        业务日志
- logs/provider.log   微信读取 Provider 日志
- logs/ai.log         DeepSeek AI 日志
- logs/scheduler.log  调度日志
- logs/email.log      邮件日志

禁止将 API Key、邮件密码等敏感信息写入日志。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_configured: set[str] = set()


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)

    for name, filename in (
        ("app", "app.log"),
        ("groupbrief.providers", "provider.log"),
        ("groupbrief.ai", "ai.log"),
        ("groupbrief.scheduler", "scheduler.log"),
        ("groupbrief.email", "email.log"),
    ):
        _configure_logger(name, logs_dir / filename, level)


def _configure_logger(name: str, file_path: Path, level: int) -> None:
    if name in _configured:
        return
    _ensure_file(file_path)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    handler = RotatingFileHandler(
        file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    _configured.add(name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
