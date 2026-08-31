"""APScheduler owner 的持久化心跳。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from app.config.settings import Settings


def heartbeat_path(settings: Settings) -> Path:
    return settings.output_dir.parent / "runtime" / "scheduler-heartbeat.json"


def record_scheduler_heartbeat(
    settings: Settings,
    *,
    job: str,
    status: str,
    detail: str = "",
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now().astimezone()
    payload = {
        "schema_version": 1,
        "owner": settings.scheduler_owner,
        "pid": os.getpid(),
        "last_beat_at": now.isoformat(),
        "last_job": str(job),
        "last_status": str(status),
        "detail": str(detail)[:300],
    }
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return payload


def load_scheduler_heartbeat(settings: Settings) -> dict:
    path = heartbeat_path(settings)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
