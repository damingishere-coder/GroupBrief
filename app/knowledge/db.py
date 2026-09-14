"""Separate, explicitly migrated schema in the existing SQLite database.

Intentionally not registered in SQLModel.metadata: importing a router must never
cause the daily service's create_all() to install optional tables.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class KnowledgeUnavailable(RuntimeError):
    pass


class Conflict(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    payload = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


MIGRATION_ID = "knowledge_001_messages"
DDL = [
    """CREATE TABLE source_batches (
        id INTEGER PRIMARY KEY, batch_key TEXT NOT NULL UNIQUE,
        group_id INTEGER REFERENCES groups(id) ON DELETE RESTRICT,
        conversation_key TEXT NOT NULL, source_scope TEXT NOT NULL,
        source_kind TEXT NOT NULL, source_locator TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL, manifest_json TEXT NOT NULL,
        range_start TEXT, range_end TEXT,
        coverage_state TEXT NOT NULL CHECK(coverage_state IN
            ('complete','complete_empty','partial','unverified','conflict')),
        record_count INTEGER NOT NULL CHECK(record_count>=0),
        fetch_meta_json TEXT NOT NULL, import_status TEXT NOT NULL
            CHECK(import_status IN ('staging','complete')),
        created_at TEXT NOT NULL,
        CHECK(range_end IS NULL OR range_start < range_end),
        UNIQUE(id,conversation_key,source_scope))""",
    "CREATE INDEX ix_source_batches_range ON source_batches(group_id,range_start,range_end)",
    "CREATE INDEX ix_source_batches_status ON source_batches(import_status,id)",
    """CREATE TABLE messages (
        id INTEGER PRIMARY KEY,
        group_id INTEGER REFERENCES groups(id) ON DELETE RESTRICT,
        conversation_key TEXT NOT NULL, source_scope TEXT NOT NULL,
        identity_key TEXT NOT NULL, identity_kind TEXT NOT NULL,
        upstream_message_id TEXT NOT NULL, sender_id TEXT NOT NULL,
        sender_name TEXT NOT NULL, sender_identity_quality TEXT NOT NULL,
        sent_at TEXT NOT NULL, message_type TEXT NOT NULL, content TEXT NOT NULL,
        content_sha256 TEXT NOT NULL, fact_sha256 TEXT NOT NULL,
        normalization_version TEXT NOT NULL, first_ingested_at TEXT NOT NULL,
        validation_state TEXT NOT NULL DEFAULT 'valid'
            CHECK(validation_state IN ('valid','conflict','ambiguous')),
        UNIQUE(conversation_key,source_scope,identity_key),
        UNIQUE(id,conversation_key,source_scope))""",
    "CREATE INDEX ix_messages_group_time ON messages(group_id,sent_at,id)",
    "CREATE INDEX ix_messages_sender_time ON messages(group_id,sender_id,sent_at,id)",
    """CREATE TABLE message_sources (
        batch_id INTEGER NOT NULL, row_ordinal INTEGER NOT NULL,
        message_id INTEGER NOT NULL, conversation_key TEXT NOT NULL,
        source_scope TEXT NOT NULL, raw_record_json TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, validation_state TEXT NOT NULL,
        PRIMARY KEY(batch_id,row_ordinal),
        FOREIGN KEY(batch_id,conversation_key,source_scope)
            REFERENCES source_batches(id,conversation_key,source_scope) ON DELETE RESTRICT,
        FOREIGN KEY(message_id,conversation_key,source_scope)
            REFERENCES messages(id,conversation_key,source_scope) ON DELETE RESTRICT)""",
    "CREATE INDEX ix_message_sources_message ON message_sources(message_id,batch_id)",
    """CREATE TABLE knowledge_jobs (
        id INTEGER PRIMARY KEY, job_key TEXT NOT NULL UNIQUE,
        job_kind TEXT NOT NULL, group_id INTEGER REFERENCES groups(id) ON DELETE RESTRICT,
        scope_json TEXT NOT NULL, input_hash TEXT NOT NULL,
        algorithm_version TEXT NOT NULL DEFAULT '1', status TEXT NOT NULL
        CHECK(status IN ('PENDING','RUNNING','WAIT_RETRY','WAIT_BUDGET','PARTIAL',
            'SUCCEEDED','HOLD_UNKNOWN','FAILED','PAUSED')),
        checkpoint_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}',
        priority INTEGER NOT NULL DEFAULT 10, lease_owner TEXT NOT NULL DEFAULT '',
        lease_token TEXT NOT NULL DEFAULT '', lease_until TEXT NOT NULL DEFAULT '',
        attempt_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '', pause_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX ix_knowledge_jobs_due ON knowledge_jobs(status,next_retry_at,priority,id)",
]
CHECKSUM = digest(DDL)


@contextmanager
def connect(path: Path, *, write: bool = False, require_schema: bool = True):
    """Never create a missing DB; sidecar writers yield quickly to daily work."""
    path = Path(path).resolve()
    mode = "rw" if write else "ro"
    try:
        con = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=0.1)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if not write:
            con.execute("PRAGMA query_only=ON")
        try:
            if require_schema:
                schema = con.execute(
                    "SELECT checksum FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
                ).fetchone()
                if not schema or schema[0] != CHECKSUM:
                    raise KnowledgeUnavailable("知识库尚未迁移或结构版本不兼容")
            yield con
        finally:
            con.close()
    except sqlite3.OperationalError as exc:
        raise KnowledgeUnavailable("知识库暂不可用：" + str(exc)) from exc


@contextmanager
def transaction(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise


def install_schema(path: Path) -> bool:
    """Explicit migration on an offline database/copy, never from app startup."""
    with connect(path, write=True, require_schema=False) as con:
        if con.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise KnowledgeUnavailable("请先完成现有核心数据库迁移")
        with transaction(con):
            existing = con.execute(
                "SELECT checksum FROM schema_migrations WHERE migration_id=?", (MIGRATION_ID,)
            ).fetchone()
            if existing:
                if existing[0] != CHECKSUM:
                    raise KnowledgeUnavailable("知识库迁移 checksum 不匹配")
                return False
            for statement in DDL:
                con.execute(statement)
            con.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (MIGRATION_ID, now_iso(), CHECKSUM))
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise KnowledgeUnavailable("迁移后外键检查失败")
    return True
