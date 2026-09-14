"""Durable, fenced sidecar jobs. Expired leases never grant old owners writes."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.knowledge.db import Conflict, canonical, connect, digest, now_iso, transaction


class LeaseLost(Conflict):
    """A stale worker must never mutate its former job."""


def enqueue(path: Path, kind: str, scope: dict, *, group_id=None, priority=10) -> dict:
    input_hash = digest(scope)
    key = digest([kind, group_id, input_hash, "1"])
    with connect(path, write=True) as con, transaction(con):
        con.execute("""INSERT INTO knowledge_jobs(job_key,job_kind,group_id,scope_json,input_hash,status,priority,created_at,updated_at)
            VALUES(?,?,?,?,?,'PENDING',?,?,?) ON CONFLICT(job_key) DO NOTHING""",
            (key, kind, group_id, canonical(scope), input_hash, priority, now_iso(), now_iso()))
        return dict(con.execute("SELECT * FROM knowledge_jobs WHERE job_key=?", (key,)).fetchone())


def claim(path: Path, owner: str, *, lease_seconds: int = 120) -> dict | None:
    now = now_iso()
    until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="microseconds")
    with connect(path, write=True) as con, transaction(con):
        # Phase 0 jobs have no external calls. AI jobs will have their own ledger
        # and are never recovered by this local-only lease rule.
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='ai_operations'").fetchone():
            from app.knowledge.ai_operations import recover
            recover(con)
        con.execute("UPDATE knowledge_jobs SET status='PENDING' WHERE status='WAIT_BUDGET' AND next_retry_at!='' AND next_retry_at<=? AND pause_requested=0",(now,))
        con.execute("""UPDATE knowledge_jobs SET status=CASE WHEN pause_requested=1 THEN 'PAUSED' ELSE 'WAIT_RETRY' END,
            lease_token='',lease_owner='',lease_until='',updated_at=?
            WHERE status='RUNNING' AND lease_until<? AND job_kind IN ('import','scan','insight','capture','index')""", (now, now))
        if con.execute("SELECT 1 FROM knowledge_jobs WHERE status='RUNNING' LIMIT 1").fetchone():
            return None
        job = con.execute("""SELECT * FROM knowledge_jobs WHERE status IN ('PENDING','WAIT_RETRY')
            AND next_retry_at<=? AND pause_requested=0 ORDER BY priority,id LIMIT 1""", (now,)).fetchone()
        if not job:
            return None
        token = uuid.uuid4().hex
        con.execute("""UPDATE knowledge_jobs SET status='RUNNING',lease_owner=?,lease_token=?,lease_until=?,
            attempt_count=attempt_count+1,updated_at=? WHERE id=?""", (owner, token, until, now, job["id"]))
        return dict(con.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job["id"],)).fetchone())


def assert_owner(con, job: dict):
    row = con.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job["id"],)).fetchone()
    if not row or row["status"] != "RUNNING" or row["lease_token"] != job["lease_token"] or row["lease_until"] <= now_iso():
        raise LeaseLost("任务租约已失效")
    return row


def heartbeat(path: Path, job: dict, checkpoint: dict) -> None:
    with connect(path, write=True) as con, transaction(con):
        row = assert_owner(con, job)
        if row["pause_requested"]:
            raise InterruptedError("任务已请求暂停")
        until = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(timespec="microseconds")
        con.execute("UPDATE knowledge_jobs SET checkpoint_json=?,lease_until=?,updated_at=? WHERE id=?",
                    (canonical(checkpoint), until, now_iso(), job["id"]))


def finish(path: Path, job: dict, status: str, result: dict, error_code="") -> None:
    with connect(path, write=True) as con, transaction(con):
        assert_owner(con, job)
        con.execute("""UPDATE knowledge_jobs SET status=?,result_json=?,error_code=?,lease_token='',lease_owner='',
            lease_until='',updated_at=? WHERE id=?""", (status, canonical(result), error_code, now_iso(), job["id"]))


def control(path: Path, job_id: int, action: str) -> dict:
    with connect(path, write=True) as con, transaction(con):
        row = con.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        if action == "pause":
            if row["status"] not in {"PENDING", "RUNNING", "WAIT_RETRY", "WAIT_BUDGET"}:
                raise Conflict("当前任务状态不可暂停")
            con.execute("UPDATE knowledge_jobs SET pause_requested=1,status=?,updated_at=? WHERE id=?",
                        ("RUNNING" if row["status"] == "RUNNING" else "PAUSED", now_iso(), job_id))
        else:
            if row["status"] not in {"FAILED", "PAUSED", "PARTIAL", "WAIT_RETRY", "WAIT_BUDGET"}:
                raise Conflict("当前任务不可安全重试；未知调用必须单独核对")
            con.execute("UPDATE knowledge_jobs SET status='PENDING',pause_requested=0,error_code='',next_retry_at='',updated_at=? WHERE id=?",
                        (now_iso(), job_id))
            if row["status"] == "PARTIAL":
                # Re-evaluate failed source items; successful batches are idempotent.
                con.execute("UPDATE knowledge_jobs SET checkpoint_json='{}' WHERE id=?", (job_id,))
        return public_job(con.execute("SELECT * FROM knowledge_jobs WHERE id=?", (job_id,)).fetchone())


def public_job(row) -> dict:
    value = dict(row)
    for name in ("lease_token", "lease_owner"):
        value.pop(name, None)
    for name in ("scope", "checkpoint", "result"):
        value[name] = json.loads(value.pop(name + "_json"))
    return value


def defer(path: Path, job: dict, reason: str, seconds=90):
    with connect(path,write=True) as con,transaction(con):
        assert_owner(con,job)
        next_time=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='microseconds')
        con.execute("""UPDATE knowledge_jobs SET status='WAIT_RETRY',next_retry_at=?,error_code=?,
          lease_token='',lease_owner='',lease_until='',updated_at=? WHERE id=?""",(next_time,reason,now_iso(),job['id']))
