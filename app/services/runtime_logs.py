"""Dashboard 使用的只读、限量、脱敏运行日志投影。"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOG_SOURCE_FILES = {
    "scheduler": "scheduler.log",
    "app": "app.log",
    "provider": "provider.log",
    "ai": "ai.log",
}
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

_LOG_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3,6})?) "
    r"\[(?P<level>[A-Z]+)] (?P<logger>[^:]+): (?P<message>.*)$"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|cookie|authorization|prompt)[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_URL_CREDENTIALS = re.compile(r"(?i)(://)[^/\s:@]+:[^@\s/]+@")
_MAX_MESSAGE_LENGTH = 500
_CURRENT_DATE_READ_LINES = 5000


def _selection(value: str | None, allowed: set[str] | frozenset[str], *, upper: bool) -> list[str]:
    if value is None or not value.strip():
        return sorted(allowed)
    selected = []
    for raw in value.split(","):
        item = raw.strip()
        item = item.upper() if upper else item.lower()
        if not item:
            continue
        if item not in allowed:
            raise ValueError(f"不支持的日志筛选值：{item}")
        if item not in selected:
            selected.append(item)
    if not selected:
        raise ValueError("日志筛选值不能为空")
    return selected


def _redact(message: str) -> tuple[str, bool]:
    value = _BEARER.sub("Bearer [REDACTED]", message)
    value = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", value)
    value = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        value,
    )
    truncated = len(value) > _MAX_MESSAGE_LENGTH
    if truncated:
        value = f"{value[:_MAX_MESSAGE_LENGTH - 1]}…"
    return value, truncated or value != message


def _parse_timestamp(value: str, timezone: ZoneInfo) -> datetime | None:
    normalized = value.replace(",", ".")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone)


def _read_lines(path: Path, *, current_date: bool) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if current_date:
                return list(deque(handle, maxlen=_CURRENT_DATE_READ_LINES))
            return list(handle)
    except OSError:
        return []


def _parse_entries(
    lines: list[str],
    *,
    source: str,
    run_date: str,
    levels: set[str],
    timezone: ZoneInfo,
) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        if current["run_date"] == run_date and current["level"] in levels:
            message, redacted_or_truncated = _redact(current["message"])
            entries.append(
                {
                    "timestamp": current["timestamp"],
                    "level": current["level"],
                    "source": source,
                    "message": message,
                    "redacted_or_truncated": redacted_or_truncated,
                    "_sort_key": current["sort_key"],
                }
            )
        current = None

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        match = _LOG_LINE.match(line)
        if match:
            flush()
            parsed = _parse_timestamp(match.group("timestamp"), timezone)
            if parsed is None:
                continue
            level = match.group("level").upper()
            if level == "WARN":
                level = "WARNING"
            current = {
                "timestamp": parsed.isoformat(),
                "run_date": parsed.date().isoformat(),
                "level": level,
                "message": match.group("message"),
                "sort_key": parsed,
            }
        elif current is not None and line:
            current["message"] = f"{current['message']}\n{line}"
    flush()
    return entries


def read_runtime_logs(
    logs_dir: Path,
    run_date: str,
    *,
    tail: int = 100,
    sources: str | None = None,
    levels: str | None = None,
    app_timezone: str = "Asia/Shanghai",
) -> dict:
    """读取固定分类日志；不接受调用方提供的文件名或路径。"""

    if tail < 1 or tail > 200:
        raise ValueError("tail 必须在 1 到 200 之间")
    try:
        timezone = ZoneInfo(app_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("日志时区配置无效") from exc

    selected_sources = _selection(sources, set(LOG_SOURCE_FILES), upper=False)
    selected_levels = set(_selection(levels, LOG_LEVELS, upper=True))
    today = datetime.now(timezone).date().isoformat()
    entries: list[dict] = []
    for source in selected_sources:
        path = Path(logs_dir) / LOG_SOURCE_FILES[source]
        entries.extend(
            _parse_entries(
                _read_lines(path, current_date=run_date == today),
                source=source,
                run_date=run_date,
                levels=selected_levels,
                timezone=timezone,
            )
        )

    entries.sort(key=lambda item: (item["_sort_key"], item["source"]))
    result_truncated = len(entries) > tail
    selected = entries[-tail:]
    sanitized = []
    for item in selected:
        row = dict(item)
        row.pop("_sort_key", None)
        sanitized.append(row)
    return {
        "run_date": run_date,
        "updated_at": datetime.now(timezone).isoformat(),
        "items": sanitized,
        "truncated": result_truncated
        or any(item["redacted_or_truncated"] for item in sanitized),
    }
