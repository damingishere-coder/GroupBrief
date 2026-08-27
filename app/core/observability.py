"""不含业务正文和凭据的结构化运行事件。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def log_event(logger, event: str, **fields: Any) -> None:
    payload = {
        "event": str(event),
        "timestamp": datetime.now().astimezone().isoformat(),
    }
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key in {"prompt", "messages", "api_key", "token", "password", "target"}:
            continue
        if key in {"error", "error_summary", "detail"}:
            value = str(value)[:300]
        payload[key] = value
    logger.info("GB_EVENT %s", json.dumps(payload, ensure_ascii=False, default=str))
