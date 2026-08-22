"""GroupBrief 日志配置。

分类日志文件：
- logs/app.log        业务日志
- logs/provider.log   微信读取 Provider 日志
- logs/ai.log         群聊总结模型日志
- logs/scheduler.log  调度日志
- logs/email.log      邮件日志

禁止将 API Key、邮件密码等敏感信息写入日志。
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_configured: set[str] = set()

# P9：日志保留天数（超出即删除）
LOG_RETENTION_DAYS = 30


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def clean_old_logs(logs_dir: Path, max_days: int = LOG_RETENTION_DAYS) -> int:
    """删除超过 max_days 的日志文件（主日志与轮转备份），防止日志无限增长。

    返回删除的文件数。
    """
    if not logs_dir.exists():
        return 0
    cutoff = time.time() - max_days * 86400
    removed = 0
    for path in logs_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".log",):
            if ".log." not in path.name:
                continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    # P9：启动时清理过期日志
    try:
        removed = clean_old_logs(logs_dir)
        if removed:
            logging.getLogger("app").info("清理过期日志文件 %d 个（保留 %d 天）", removed, LOG_RETENTION_DAYS)
    except Exception:
        pass

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
