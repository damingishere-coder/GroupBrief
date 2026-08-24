"""GroupBrief 的显式离线数据库迁移。

该模块故意不接入应用启动流程。迁移只读取明确指定的源数据库，并把结果
写入一个全新的目标文件；源数据库不会被原地修改或覆盖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


MIGRATION_ID = "p0_2b_group_run_identity_v1"
TARGET_USER_VERSION = 1
_MIGRATION_SIGNATURE = """group_runs:nullable-group,legacy-id,identity-state,restrict-fks
reports:unique-group-run,restrict-fk
execution_logs:nullable-run,restrict-fk
indexes:runs-date-status,execution-run,active-wechat-id
"""
MIGRATION_CHECKSUM = hashlib.sha256(_MIGRATION_SIGNATURE.encode("utf-8")).hexdigest()

_CORE_TABLES = ("groups", "runs", "group_runs", "reports", "execution_logs")
_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "groups": {"id", "wechat_group_id", "deleted_at"},
    "runs": {"id", "report_date", "status"},
    "group_runs": {
        "id",
        "run_id",
        "group_id",
        "provider_used",
        "message_count",
        "speaker_count",
        "ranking_status",
        "prompt_status",
        "error_message",
    },
    "reports": {
        "id",
        "group_run_id",
        "ranking_text",
        "prompt_text",
        "ranking_file",
        "prompt_file",
        "poster_file",
        "poster_status",
        "email_status",
        "created_at",
        "updated_at",
    },
    "execution_logs": {"id", "run_id", "level", "message", "created_at"},
}
_REBUILT_TABLES = ("group_runs", "reports", "execution_logs")
_KNOWN_REBUILT_INDEXES = {
    "ix_group_runs_group_id",
    "ix_group_runs_run_id",
    "ix_reports_group_run_id",
}


class MigrationError(RuntimeError):
    """迁移前置条件、执行或验证失败。"""


@dataclass(frozen=True)
class DatabaseSnapshot:
    integrity_check: str
    user_version: int
    table_counts: dict[str, int]
    orphan_group_runs: int
    orphan_reports: int
    group_runs_missing_run: int
    execution_logs_missing_run: int
    duplicate_report_relations: int
    duplicate_active_wechat_ids: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _scalar(connection: sqlite3.Connection, sql: str) -> int:
    return int(connection.execute(sql).fetchone()[0])


def _validate_source_schema(connection: sqlite3.Connection) -> None:
    missing_tables = [table for table in _CORE_TABLES if not _table_exists(connection, table)]
    if missing_tables:
        raise MigrationError(f"数据库缺少必要表：{', '.join(missing_tables)}")

    if _table_exists(connection, "schema_migrations"):
        migration_columns = _table_columns(connection, "schema_migrations")
        expected = {"migration_id", "applied_at", "checksum"}
        if not expected.issubset(migration_columns):
            raise MigrationError("schema_migrations 表结构不兼容，拒绝继续")
        applied = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if applied is not None:
            raise MigrationError(f"迁移 {MIGRATION_ID} 已经执行，拒绝重复迁移")

    for table, required in _REQUIRED_COLUMNS.items():
        actual = _table_columns(connection, table)
        missing = sorted(required - actual)
        if missing:
            raise MigrationError(f"表 {table} 缺少必要列：{', '.join(missing)}")
        if table in _REBUILT_TABLES:
            unexpected = sorted(actual - required)
            if unexpected:
                raise MigrationError(
                    f"待重建表 {table} 包含未知列，拒绝静默丢弃：{', '.join(unexpected)}"
                )

    unknown_indexes = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='index'
              AND tbl_name IN ('group_runs', 'reports', 'execution_logs')
              AND sql IS NOT NULL
            ORDER BY name
            """
        )
        if str(row[0]) not in _KNOWN_REBUILT_INDEXES
    ]
    if unknown_indexes:
        raise MigrationError(
            "待重建表包含未知显式索引，拒绝静默丢弃：" + "，".join(unknown_indexes)
        )

    dependent_objects = [
        f"{row[0]}:{row[1]}"
        for row in connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE
                (type='trigger' AND tbl_name IN ('group_runs', 'reports', 'execution_logs'))
                OR
                (
                    type='view'
                    AND (
                        LOWER(sql) LIKE '%group_runs%'
                        OR LOWER(sql) LIKE '%reports%'
                        OR LOWER(sql) LIKE '%execution_logs%'
                    )
                )
            ORDER BY type, name
            """
        )
    ]
    if dependent_objects:
        raise MigrationError(
            "待重建表存在未知触发器或依赖视图，拒绝静默破坏：" + "，".join(dependent_objects)
        )

def _validate_no_sqlite_sidecars(source: Path) -> None:
    sidecars = [
        Path(f"{source}-wal"),
        Path(f"{source}-shm"),
        Path(f"{source}-journal"),
    ]
    present = [path.name for path in sidecars if path.exists()]
    if present:
        raise MigrationError(
            "源数据库存在 SQLite 写入/日志侧文件，不能视为离线源：" + "，".join(present)
        )


def _snapshot(connection: sqlite3.Connection) -> DatabaseSnapshot:
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    integrity = "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows)
    counts = {table: _scalar(connection, f'SELECT COUNT(*) FROM "{table}"') for table in _CORE_TABLES}
    return DatabaseSnapshot(
        integrity_check=integrity,
        user_version=int(connection.execute("PRAGMA user_version").fetchone()[0]),
        table_counts=counts,
        orphan_group_runs=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM group_runs gr
            LEFT JOIN groups g ON g.id = gr.group_id
            WHERE g.id IS NULL
            """,
        ),
        orphan_reports=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM reports rep
            LEFT JOIN group_runs gr ON gr.id = rep.group_run_id
            WHERE gr.id IS NULL
            """,
        ),
        group_runs_missing_run=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM group_runs gr
            LEFT JOIN runs r ON r.id = gr.run_id
            WHERE r.id IS NULL
            """,
        ),
        execution_logs_missing_run=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM execution_logs log
            LEFT JOIN runs r ON r.id = log.run_id
            WHERE log.run_id IS NOT NULL AND r.id IS NULL
            """,
        ),
        duplicate_report_relations=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM (
                SELECT group_run_id
                FROM reports
                GROUP BY group_run_id
                HAVING COUNT(*) > 1
            )
            """,
        ),
        duplicate_active_wechat_ids=_scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM (
                SELECT wechat_group_id
                FROM groups
                WHERE TRIM(wechat_group_id) <> '' AND deleted_at IS NULL
                GROUP BY wechat_group_id
                HAVING COUNT(*) > 1
            )
            """,
        ),
    )


def _validate_preflight(snapshot: DatabaseSnapshot) -> None:
    if snapshot.integrity_check != "ok":
        raise MigrationError(f"源数据库完整性检查失败：{snapshot.integrity_check}")
    if snapshot.user_version != 0:
        raise MigrationError(
            f"源数据库 user_version={snapshot.user_version}，本迁移只接受旧版 user_version=0"
        )
    failures = {
        "缺失父 Run 的 GroupRun": snapshot.group_runs_missing_run,
        "孤儿 Report": snapshot.orphan_reports,
        "同一 GroupRun 的重复 Report": snapshot.duplicate_report_relations,
        "重复的活动微信群 ID": snapshot.duplicate_active_wechat_ids,
        "缺失父 Run 的执行日志": snapshot.execution_logs_missing_run,
    }
    present = [f"{name}={count}" for name, count in failures.items() if count]
    if present:
        raise MigrationError("迁移前置检查失败：" + "，".join(present))


def preflight_database(source: str | Path) -> dict[str, Any]:
    """只读检查一个候选源数据库，不创建任何输出文件。"""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise MigrationError(f"源数据库不存在或不是文件：{source_path}")
    _validate_no_sqlite_sidecars(source_path)
    with _connect_read_only(source_path) as connection:
        _validate_source_schema(connection)
        snapshot = _snapshot(connection)
    _validate_preflight(snapshot)
    return {
        "migration_id": MIGRATION_ID,
        "source": str(source_path),
        "source_sha256": _sha256(source_path),
        "snapshot": asdict(snapshot),
        "ready": True,
    }


def _backup_database(source: Path, destination: Path) -> None:
    source_connection = _connect_read_only(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def _apply_relationship_migration(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")

        connection.execute("ALTER TABLE reports RENAME TO reports_p0_2b_legacy")
        connection.execute("ALTER TABLE execution_logs RENAME TO execution_logs_p0_2b_legacy")
        connection.execute("ALTER TABLE group_runs RENAME TO group_runs_p0_2b_legacy")

        connection.execute(
            """
            CREATE TABLE group_runs (
                id INTEGER NOT NULL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                group_id INTEGER REFERENCES groups(id) ON DELETE RESTRICT,
                legacy_group_id INTEGER,
                identity_state TEXT NOT NULL DEFAULT 'linked',
                orphan_reason TEXT NOT NULL DEFAULT '',
                provider_used VARCHAR NOT NULL,
                message_count INTEGER NOT NULL,
                speaker_count INTEGER NOT NULL,
                ranking_status VARCHAR NOT NULL,
                prompt_status VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL,
                CHECK (
                    (
                        identity_state = 'linked'
                        AND group_id IS NOT NULL
                        AND legacy_group_id IS NULL
                        AND orphan_reason = ''
                    )
                    OR
                    (
                        identity_state = 'orphaned'
                        AND group_id IS NULL
                        AND legacy_group_id IS NOT NULL
                        AND orphan_reason = 'historical_group_missing'
                    )
                )
            )
            """
        )
        connection.execute(
            """
            INSERT INTO group_runs (
                id, run_id, group_id, legacy_group_id, identity_state,
                orphan_reason, provider_used, message_count, speaker_count,
                ranking_status, prompt_status, error_message
            )
            SELECT
                gr.id,
                gr.run_id,
                CASE WHEN g.id IS NULL THEN NULL ELSE gr.group_id END,
                CASE WHEN g.id IS NULL THEN gr.group_id ELSE NULL END,
                CASE WHEN g.id IS NULL THEN 'orphaned' ELSE 'linked' END,
                CASE WHEN g.id IS NULL THEN 'historical_group_missing' ELSE '' END,
                gr.provider_used,
                gr.message_count,
                gr.speaker_count,
                gr.ranking_status,
                gr.prompt_status,
                gr.error_message
            FROM group_runs_p0_2b_legacy gr
            LEFT JOIN groups g ON g.id = gr.group_id
            """
        )

        connection.execute(
            """
            CREATE TABLE reports (
                id INTEGER NOT NULL PRIMARY KEY,
                group_run_id INTEGER NOT NULL UNIQUE
                    REFERENCES group_runs(id) ON DELETE RESTRICT,
                ranking_text VARCHAR NOT NULL,
                prompt_text VARCHAR NOT NULL,
                ranking_file VARCHAR NOT NULL,
                prompt_file VARCHAR NOT NULL,
                poster_file VARCHAR NOT NULL,
                poster_status VARCHAR NOT NULL,
                email_status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO reports (
                id, group_run_id, ranking_text, prompt_text, ranking_file,
                prompt_file, poster_file, poster_status, email_status,
                created_at, updated_at
            )
            SELECT
                id, group_run_id, ranking_text, prompt_text, ranking_file,
                prompt_file, poster_file, poster_status, email_status,
                created_at, updated_at
            FROM reports_p0_2b_legacy
            """
        )

        connection.execute(
            """
            CREATE TABLE execution_logs (
                id INTEGER NOT NULL PRIMARY KEY,
                run_id INTEGER REFERENCES runs(id) ON DELETE RESTRICT,
                level VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO execution_logs (id, run_id, level, message, created_at)
            SELECT id, run_id, level, message, created_at
            FROM execution_logs_p0_2b_legacy
            """
        )

        connection.execute("DROP TABLE reports_p0_2b_legacy")
        connection.execute("DROP TABLE execution_logs_p0_2b_legacy")
        connection.execute("DROP TABLE group_runs_p0_2b_legacy")

        connection.execute("CREATE INDEX ix_group_runs_run_id ON group_runs(run_id)")
        connection.execute("CREATE INDEX ix_group_runs_group_id ON group_runs(group_id)")
        connection.execute("CREATE INDEX ix_runs_report_date_status ON runs(report_date, status)")
        connection.execute("CREATE INDEX ix_execution_logs_run_id ON execution_logs(run_id)")
        connection.execute(
            """
            CREATE UNIQUE INDEX uq_groups_wechat_group_id_active
            ON groups(wechat_group_id)
            WHERE TRIM(wechat_group_id) <> '' AND deleted_at IS NULL
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT NOT NULL PRIMARY KEY,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations(migration_id, applied_at, checksum) VALUES (?, ?, ?)",
            (MIGRATION_ID, datetime.now(timezone.utc).isoformat(), MIGRATION_CHECKSUM),
        )
        connection.execute(f"PRAGMA user_version = {TARGET_USER_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _validate_migrated_database(database: Path, before: DatabaseSnapshot) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            raise MigrationError("迁移后完整性检查失败：" + "; ".join(integrity_rows))

        foreign_key_rows = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if foreign_key_rows:
            raise MigrationError(f"迁移后外键检查失败，共 {len(foreign_key_rows)} 行")

        counts = {table: _scalar(connection, f'SELECT COUNT(*) FROM "{table}"') for table in _CORE_TABLES}
        if counts != before.table_counts:
            raise MigrationError(f"迁移前后核心表行数不一致：before={before.table_counts}, after={counts}")

        orphaned = _scalar(connection, "SELECT COUNT(*) FROM group_runs WHERE identity_state='orphaned'")
        linked = _scalar(connection, "SELECT COUNT(*) FROM group_runs WHERE identity_state='linked'")
        invalid_links = _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM group_runs gr
            LEFT JOIN groups g ON g.id = gr.group_id
            WHERE gr.group_id IS NOT NULL AND g.id IS NULL
            """,
        )
        preserved_legacy_ids = _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM group_runs
            WHERE identity_state='orphaned'
              AND group_id IS NULL
              AND legacy_group_id IS NOT NULL
              AND orphan_reason='historical_group_missing'
            """,
        )
        if orphaned != before.orphan_group_runs or preserved_legacy_ids != before.orphan_group_runs:
            raise MigrationError("历史孤儿数量或旧 ID 保留数量不一致")
        if linked != before.table_counts["group_runs"] - before.orphan_group_runs:
            raise MigrationError("可关联 GroupRun 数量不一致")
        if invalid_links:
            raise MigrationError(f"迁移后仍存在 {invalid_links} 条无效活动 group_id")

        migration_row = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if migration_row is None or str(migration_row[0]) != MIGRATION_CHECKSUM:
            raise MigrationError("schema_migrations 记录缺失或 checksum 不一致")
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != TARGET_USER_VERSION:
            raise MigrationError(f"user_version 不正确：{user_version}")

        foreign_keys = {
            table: [dict(row) for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')]
            for table in ("group_runs", "reports", "execution_logs")
        }
        actual_foreign_keys = {
            (table, str(row["from"]), str(row["table"]), str(row["to"]), str(row["on_delete"]))
            for table, rows in foreign_keys.items()
            for row in rows
        }
        expected_foreign_keys = {
            ("group_runs", "run_id", "runs", "id", "RESTRICT"),
            ("group_runs", "group_id", "groups", "id", "RESTRICT"),
            ("reports", "group_run_id", "group_runs", "id", "RESTRICT"),
            ("execution_logs", "run_id", "runs", "id", "RESTRICT"),
        }
        if actual_foreign_keys != expected_foreign_keys:
            raise MigrationError(
                f"迁移后外键结构不符合预期：actual={sorted(actual_foreign_keys)}"
            )
        return {
            "integrity_check": "ok",
            "foreign_key_check_rows": 0,
            "table_counts": counts,
            "linked_group_runs": linked,
            "orphaned_group_runs": orphaned,
            "preserved_legacy_group_ids": preserved_legacy_ids,
            "invalid_linked_group_ids": invalid_links,
            "user_version": user_version,
            "foreign_keys": foreign_keys,
        }
    finally:
        connection.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_and_validate_targets(
    source: str | Path,
    output: str | Path,
    manifest: str | Path | None,
) -> tuple[Path, Path, Path]:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    manifest_path = (
        Path(manifest).expanduser().resolve()
        if manifest is not None
        else output_path.with_suffix(output_path.suffix + ".manifest.json")
    )
    if source_path == output_path:
        raise MigrationError("源数据库和输出数据库不能是同一个文件")
    if manifest_path in {source_path, output_path}:
        raise MigrationError("Manifest 路径不能与源数据库或输出数据库相同")
    if output_path.exists():
        raise MigrationError(f"输出文件已存在，拒绝覆盖：{output_path}")
    if manifest_path.exists():
        raise MigrationError(f"Manifest 已存在，拒绝覆盖：{manifest_path}")
    return source_path, output_path, manifest_path


def migrate_database(
    source: str | Path,
    output: str | Path,
    *,
    manifest: str | Path | None = None,
) -> dict[str, Any]:
    """将旧数据库迁移到一个全新的输出文件，并返回验证 Manifest。"""
    source_path, output_path, manifest_path = _resolve_and_validate_targets(
        source,
        output,
        manifest,
    )

    preflight = preflight_database(source_path)
    before = DatabaseSnapshot(**preflight["snapshot"])
    source_hash_before = str(preflight["source_sha256"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temporary_database = output_path.with_name(f".{output_path.name}.{token}.tmp")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{token}.tmp")
    output_promoted = False
    manifest_promoted = False
    try:
        _backup_database(source_path, temporary_database)
        _apply_relationship_migration(temporary_database)
        after = _validate_migrated_database(temporary_database, before)

        source_hash_after = _sha256(source_path)
        if source_hash_after != source_hash_before:
            raise MigrationError("迁移期间源数据库文件发生变化，拒绝产出结果")

        result: dict[str, Any] = {
            "migration_id": MIGRATION_ID,
            "migration_checksum": MIGRATION_CHECKSUM,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": str(source_path),
            "output": str(output_path),
            "manifest": str(manifest_path),
            "source_sha256_before": source_hash_before,
            "source_sha256_after": source_hash_after,
            "source_unchanged": True,
            "output_sha256": _sha256(temporary_database),
            "before": asdict(before),
            "after": after,
        }
        _write_json(temporary_manifest, result)
        os.replace(temporary_database, output_path)
        output_promoted = True
        os.replace(temporary_manifest, manifest_path)
        manifest_promoted = True
        return result
    except Exception:
        for temporary_path in (temporary_database, temporary_manifest):
            if temporary_path.exists():
                temporary_path.unlink()
        if output_promoted and not manifest_promoted and output_path.exists():
            output_path.unlink()
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 GroupBrief 旧数据库迁移到一个全新的、经过校验的输出副本。",
    )
    parser.add_argument("--source", required=True, type=Path, help="只读源数据库路径")
    parser.add_argument("--output", required=True, type=Path, help="必须不存在的输出数据库路径")
    parser.add_argument("--manifest", type=Path, help="可选 Manifest 路径")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--dry-run", action="store_true", help="仅执行只读前置检查")
    actions.add_argument("--apply", action="store_true", help="创建并迁移新的输出副本")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.dry_run:
            source_path, output_path, _ = _resolve_and_validate_targets(
                args.source,
                args.output,
                args.manifest,
            )
            result = preflight_database(source_path)
            result["planned_output"] = str(output_path)
        else:
            result = migrate_database(args.source, args.output, manifest=args.manifest)
    except (MigrationError, OSError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
