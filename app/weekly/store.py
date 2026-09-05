"""周报独立状态与工件存储。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from app.core.path_security import resolve_within, validate_iso_date

_LOCK = threading.RLock()


class WeeklyStore:
    def __init__(self, output_root: Path | str):
        self.root = Path(output_root) / ".weekly"

    def period_dir(self, week_start: str, week_end: str) -> Path:
        start = validate_iso_date(week_start, field_name="week_start")
        end = validate_iso_date(week_end, field_name="week_end")
        return resolve_within(self.root, f"{start}_{end}")

    def group_dir(self, week_start: str, week_end: str, group_id: int) -> Path:
        if int(group_id) <= 0:
            raise ValueError("group_id 必须为正整数")
        return resolve_within(self.period_dir(week_start, week_end), f"group-{int(group_id)}")

    def state_path(self, week_start: str, week_end: str, group_id: int) -> Path:
        return self.group_dir(week_start, week_end, group_id) / "weekly.json"

    def text_path(self, week_start: str, week_end: str, group_id: int) -> Path:
        return self.group_dir(week_start, week_end, group_id) / "weekly.txt"

    def card_path(self, week_start: str, week_end: str, group_id: int) -> Path:
        return self.group_dir(week_start, week_end, group_id) / "weekly_card.png"

    def load(self, week_start: str, week_end: str, group_id: int) -> dict:
        path = self.state_path(week_start, week_end, group_id)
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return self._corrupt(week_start, week_end, group_id, "read_or_json_invalid")
        if not isinstance(value, dict):
            return self._corrupt(week_start, week_end, group_id, "root_not_object")
        if (
            value.get("week_start") != week_start
            or value.get("week_end") != week_end
            or value.get("group_id") != group_id
        ):
            return self._corrupt(week_start, week_end, group_id, "identity_invalid")
        return value

    @staticmethod
    def _corrupt(week_start: str, week_end: str, group_id: int, reason: str) -> dict:
        return {
            "schema_version": 1,
            "week_start": week_start,
            "week_end": week_end,
            "group_id": group_id,
            "group_name": f"群 {group_id}",
            "status": "needs_attention",
            "error_type": "WEEKLY_STATE_CORRUPT",
            "stage": "state",
            "retryable": False,
            "state_error_reason": reason,
        }

    def save(self, week_start: str, week_end: str, group_id: int, value: dict) -> dict:
        with _LOCK:
            path = self.state_path(week_start, week_end, group_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(value)
            payload.update(
                schema_version=1,
                week_start=week_start,
                week_end=week_end,
                group_id=int(group_id),
            )
            temp = path.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(path)
            return payload

    def update(self, week_start: str, week_end: str, group_id: int, **fields) -> dict:
        with _LOCK:
            current = self.load(week_start, week_end, group_id)
            current.update(fields)
            return self.save(week_start, week_end, group_id, current)

    def claim_send(
        self,
        week_start: str,
        week_end: str,
        group_id: int,
        *,
        now: datetime,
        lease_seconds: int = 600,
    ) -> tuple[str | None, dict]:
        """只允许 READY 周报原子进入 sending；崩溃后绝不自动重提。"""
        with _LOCK:
            current = self.load(week_start, week_end, group_id)
            if current.get("status") != "ready_to_send":
                return None, current
            claim_id = uuid.uuid4().hex
            current.update(
                status="sending",
                send_claim_id=claim_id,
                send_attempt_started_at=now.isoformat(),
                send_claim_expires_at=(
                    now + timedelta(seconds=max(int(lease_seconds), 60))
                ).isoformat(),
            )
            return claim_id, self.save(week_start, week_end, group_id, current)

    def list_states(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        states: list[dict] = []
        for path in self.root.glob("*_*/group-*/weekly.json"):
            try:
                period = path.parent.parent.name
                week_start, week_end = period.split("_", 1)
                group_id = int(path.parent.name.removeprefix("group-"))
                value = self.load(week_start, week_end, group_id)
            except (ValueError, OSError):
                continue
            if isinstance(value, dict):
                states.append(value)
        states.sort(
            key=lambda item: (
                str(item.get("week_start") or ""),
                int(item.get("group_id") or 0) if str(item.get("group_id") or "").isdigit() else 0,
            ),
            reverse=True,
        )
        return states
