"""文件型 RepairIncident 账本：跨进程原子、脱敏、去重与熔断。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.v2.run_store import _run_mutex

_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|password|cookie|authorization)\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_UNKNOWN_OR_DANGEROUS = {
    "SEND_RESULT_UNKNOWN",
    "PROMPT_RESULT_UNKNOWN",
    "WEEKLY_SEND_RESULT_UNKNOWN",
    "WEEKLY_AI_RESULT_UNKNOWN",
    "SCHEDULER_STATE_CORRUPT",
    "RUN_STATE_CORRUPT",
    "WEEKLY_STATE_CORRUPT",
    "GROUP_TARGET_MISMATCH",
    "WECHAT_TARGET_AMBIGUOUS",
    "WECHAT_TARGET_NOT_FOUND",
}
_ENVIRONMENT_ERRORS = {
    "WEEKLY_AI_FAILED_FALLBACK",
    "WECHAT_OFFLINE",
    "WECHAT_DATA_UNAVAILABLE",
}


def sanitize_text(value: object, *, limit: int = 500) -> str:
    text = str(value or "").replace("\x00", " ")
    text = _SECRET.sub(r"\1=[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    return " ".join(text.split())[:limit]


def repair_mode_for(scope: str, error_type: str) -> str:
    code = str(error_type or "").upper()
    if scope == "repair":
        return "diagnostic_only"
    if code in _UNKNOWN_OR_DANGEROUS or "UNKNOWN" in code or "CORRUPT" in code:
        return "diagnostic_only"
    if code in _ENVIRONMENT_ERRORS:
        return "environment"
    if scope in {"send", "wechat"}:
        return "environment"
    return "code_fix"


class RepairIncidentStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = Path(settings.output_dir) / ".repair"
        self.incidents_dir = self.root / "incidents"
        self.state_path = self.root / "controller.json"
        self.lock_path = self.root / "admission.lock"

    @staticmethod
    def _now(now: datetime | None = None) -> datetime:
        return now or datetime.now().astimezone()

    def _write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def _read(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def list_incidents(self) -> list[dict]:
        if not self.incidents_dir.is_dir():
            return []
        items: list[dict] = []
        for path in self.incidents_dir.glob("*.json"):
            value = self._read(path)
            if value:
                items.append(value)
            else:
                items.append(
                    {
                        "incident_id": path.stem,
                        "status": "corrupt",
                        "repair_mode": "diagnostic_only",
                        "error_type": "REPAIR_INCIDENT_CORRUPT",
                    }
                )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return items

    def get(self, incident_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", str(incident_id or "")):
            return {}
        return self._read(self.incidents_dir / f"{incident_id}.json")

    def save(self, incident: dict) -> dict:
        incident_id = str(incident.get("incident_id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", incident_id):
            raise ValueError("incident_id 无效")
        incident = dict(incident)
        incident["updated_at"] = self._now().isoformat()
        self._write(self.incidents_dir / f"{incident_id}.json", incident)
        return incident

    def record(
        self,
        *,
        scope: str,
        error_type: str,
        stage: str,
        source_path: str,
        error_summary: str = "",
        stack_summary: str = "",
        related_commit_sha: str = "",
        now: datetime | None = None,
    ) -> dict:
        now = self._now(now)
        safe_scope = sanitize_text(scope, limit=40).lower() or "unknown"
        safe_error = sanitize_text(error_type, limit=80).upper() or "UNKNOWN_ERROR"
        safe_stage = sanitize_text(stage, limit=40).lower() or "unknown"
        safe_source = sanitize_text(source_path, limit=180).replace("\\", "/")
        if ":/" in safe_source or safe_source.startswith("/"):
            safe_source = Path(safe_source).name
        summary = sanitize_text(error_summary)
        stack = sanitize_text(stack_summary)
        canonical = "|".join(
            (safe_scope, safe_error, safe_stage, stack or summary, related_commit_sha[:40])
        )
        fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        cutoff = now - timedelta(days=max(int(self.settings.repair_fingerprint_cooldown_days), 1))
        with _run_mutex(self.lock_path):
            for existing in self.list_incidents():
                if existing.get("fingerprint") != fingerprint:
                    continue
                try:
                    created = datetime.fromisoformat(str(existing.get("created_at") or ""))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=now.tzinfo)
                except ValueError:
                    continue
                if created >= cutoff:
                    existing["last_seen_at"] = now.isoformat()
                    existing["occurrence_count"] = int(existing.get("occurrence_count") or 1) + 1
                    return self.save(existing)
            incident_id = uuid.uuid4().hex
            mode = repair_mode_for(safe_scope, safe_error)
            incident = {
                "schema_version": 1,
                "incident_id": incident_id,
                "fingerprint": fingerprint,
                "scope": safe_scope,
                "error_type": safe_error,
                "stage": safe_stage,
                "source_path": safe_source,
                "related_commit_sha": sanitize_text(related_commit_sha, limit=40),
                "redacted_error_summary": summary,
                "redacted_stack_summary": stack,
                "repair_mode": mode,
                "status": "queued" if mode == "code_fix" else mode,
                "attempt_count": 0,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "last_seen_at": now.isoformat(),
                "occurrence_count": 1,
                "cooldown_until": "",
                "codex_thread_id": "",
                "branch": "",
                "commit_sha": "",
                "pr_url": "",
                "test_result": {},
                "circuit_breaker_reason": "",
            }
            self._write(self.incidents_dir / f"{incident_id}.json", incident)
            return incident

    def controller_state(self) -> dict:
        return self._read(self.state_path)

    def start_next(self, *, now: datetime | None = None) -> tuple[dict | None, str]:
        now = self._now(now)
        with _run_mutex(self.lock_path):
            state = self.controller_state()
            try:
                circuit_until = datetime.fromisoformat(str(state.get("circuit_until") or ""))
            except (TypeError, ValueError):
                circuit_until = None
            if circuit_until and circuit_until > now:
                return None, "circuit_open"
            today = now.date().isoformat()
            attempts = [value for value in state.get("attempts", []) if str(value).startswith(today)]
            if len(attempts) >= max(int(self.settings.repair_max_per_day), 1):
                return None, "daily_limit"
            queued = [item for item in self.list_incidents() if item.get("status") == "queued"]
            if not queued:
                return None, "empty"
            incident = sorted(queued, key=lambda item: str(item.get("created_at") or ""))[0]
            incident.update(
                status="running",
                attempt_count=int(incident.get("attempt_count") or 0) + 1,
                last_attempt_at=now.isoformat(),
            )
            self.save(incident)
            state["active_incident_id"] = incident["incident_id"]
            state["active_fingerprint"] = incident["fingerprint"]
            state["attempts"] = (list(state.get("attempts") or []) + [now.isoformat()])[-30:]
            self._write(self.state_path, state)
            return incident, "started"

    def finish(self, incident: dict, *, success: bool, reason: str = "", now: datetime | None = None) -> dict:
        now = self._now(now)
        with _run_mutex(self.lock_path):
            state = self.controller_state()
            streak = 0 if success else int(state.get("failure_streak") or 0) + 1
            state.update(active_incident_id="", active_fingerprint="", failure_streak=streak)
            if not success and streak >= max(int(self.settings.repair_circuit_failure_threshold), 1):
                until = now + timedelta(hours=max(int(self.settings.repair_circuit_cooldown_hours), 1))
                state["circuit_until"] = until.isoformat()
                state["circuit_reason"] = sanitize_text(reason)
                incident["circuit_breaker_reason"] = state["circuit_reason"]
                incident["cooldown_until"] = until.isoformat()
            self._write(self.state_path, state)
            incident["status"] = "pr_created" if success else "failed"
            if reason:
                incident["last_error"] = sanitize_text(reason)
            return self.save(incident)

    def summary(self) -> dict:
        items = self.list_incidents()
        state = self.controller_state()
        now = self._now()
        circuit_until = str(state.get("circuit_until") or "")
        try:
            circuit_open = datetime.fromisoformat(circuit_until) > now
        except (TypeError, ValueError):
            circuit_open = False
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "enabled": bool(self.settings.repair_enabled),
            "queued": sum(item.get("status") == "queued" for item in items),
            "active_fingerprint": str(state.get("active_fingerprint") or ""),
            "circuit_open": circuit_open,
            "circuit_until": circuit_until,
            "circuit_reason": str(state.get("circuit_reason") or ""),
            "incident_count": len(items),
            "status_counts": counts,
        }


def public_incident(value: dict) -> dict:
    allowed = {
        "schema_version", "incident_id", "fingerprint", "scope", "error_type",
        "stage", "source_path", "repair_mode", "status", "attempt_count",
        "created_at", "updated_at", "last_seen_at", "occurrence_count",
        "cooldown_until", "codex_thread_id", "branch", "commit_sha", "pr_url",
        "test_result", "circuit_breaker_reason", "last_error",
    }
    return {key: value.get(key) for key in allowed if key in value}
