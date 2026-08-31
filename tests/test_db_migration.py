from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.db import offline_migrations as migrations


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_legacy_database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE groups (
                id INTEGER PRIMARY KEY,
                wechat_group_id VARCHAR NOT NULL,
                deleted_at DATETIME
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY,
                report_date VARCHAR NOT NULL,
                status VARCHAR NOT NULL
            );
            CREATE TABLE group_runs (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                provider_used VARCHAR NOT NULL,
                message_count INTEGER NOT NULL,
                speaker_count INTEGER NOT NULL,
                ranking_status VARCHAR NOT NULL,
                prompt_status VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL
            );
            CREATE TABLE reports (
                id INTEGER PRIMARY KEY,
                group_run_id INTEGER NOT NULL,
                ranking_text VARCHAR NOT NULL,
                prompt_text VARCHAR NOT NULL,
                ranking_file VARCHAR NOT NULL,
                prompt_file VARCHAR NOT NULL,
                poster_file VARCHAR NOT NULL,
                poster_status VARCHAR NOT NULL,
                email_status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE execution_logs (
                id INTEGER PRIMARY KEY,
                run_id INTEGER,
                level VARCHAR NOT NULL,
                message VARCHAR NOT NULL,
                created_at DATETIME NOT NULL
            );

            INSERT INTO groups(id, wechat_group_id, deleted_at)
            VALUES (1, 'active@chatroom', NULL);
            INSERT INTO runs(id, report_date, status)
            VALUES (1, '2026-08-24', 'success');
            INSERT INTO group_runs(
                id, run_id, group_id, provider_used, message_count,
                speaker_count, ranking_status, prompt_status, error_message
            ) VALUES
                (10, 1, 1, 'mock', 12, 3, 'success', 'success', ''),
                (11, 1, 99, 'mock', 8, 2, 'success', 'success', '');
            INSERT INTO reports(
                id, group_run_id, ranking_text, prompt_text, ranking_file,
                prompt_file, poster_file, poster_status, email_status,
                created_at, updated_at
            ) VALUES
                (20, 10, 'linked', 'linked prompt', '', '', '', '', '',
                    '2026-08-24 00:00:00', '2026-08-24 00:00:00'),
                (21, 11, 'orphaned', 'orphaned prompt', '', '', '', '', '',
                    '2026-08-24 00:00:00', '2026-08-24 00:00:00');
            INSERT INTO execution_logs(id, run_id, level, message, created_at)
            VALUES (30, 1, 'info', 'test', '2026-08-24 00:00:00');
            """
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_migration_preserves_history_and_adds_constraints(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    output = tmp_path / "migrated.db"
    source_hash = _hash(source)

    result = migrations.migrate_database(source, output)

    assert output.is_file()
    assert _hash(source) == source_hash
    assert result["source_unchanged"] is True
    assert result["before"]["orphan_group_runs"] == 1
    assert result["after"]["orphaned_group_runs"] == 1
    assert result["after"]["linked_group_runs"] == 1
    assert result["after"]["table_counts"] == result["before"]["table_counts"]

    manifest = output.with_suffix(".db.manifest.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["output_sha256"] == _hash(output)
    assert payload["migration_id"] == migrations.MIGRATION_ID

    connection = sqlite3.connect(output)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        linked = connection.execute(
            "SELECT group_id, legacy_group_id, identity_state, orphan_reason FROM group_runs WHERE id=10"
        ).fetchone()
        orphaned = connection.execute(
            "SELECT group_id, legacy_group_id, identity_state, orphan_reason FROM group_runs WHERE id=11"
        ).fetchone()
        assert linked == (1, None, "linked", "")
        assert orphaned == (None, 99, "orphaned", "historical_group_missing")
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM groups WHERE id=1")
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO reports(
                    id, group_run_id, ranking_text, prompt_text, ranking_file,
                    prompt_file, poster_file, poster_status, email_status,
                    created_at, updated_at
                ) VALUES (22, 10, '', '', '', '', '', '', '', '', '')
                """
            )
    finally:
        connection.close()


def test_preflight_is_read_only_and_reports_orphans(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    before = _hash(source)

    result = migrations.preflight_database(source)

    assert result["ready"] is True
    assert result["snapshot"]["user_version"] == 0
    assert result["snapshot"]["orphan_group_runs"] == 1
    assert result["snapshot"]["orphan_reports"] == 0
    assert _hash(source) == before


@pytest.mark.parametrize("conflict", ["same", "existing_output", "existing_manifest"])
def test_migration_refuses_destructive_path_conflicts(tmp_path: Path, conflict: str) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    output = source if conflict == "same" else tmp_path / "migrated.db"
    manifest = tmp_path / "manifest.json"
    if conflict == "existing_output":
        output.write_bytes(b"do not overwrite")
    if conflict == "existing_manifest":
        manifest.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(migrations.MigrationError):
        migrations.migrate_database(source, output, manifest=manifest)

    if conflict == "existing_output":
        assert output.read_bytes() == b"do not overwrite"
    if conflict == "existing_manifest":
        assert manifest.read_text(encoding="utf-8") == "do not overwrite"


def test_migration_rejects_orphan_report_without_creating_output(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            INSERT INTO reports(
                id, group_run_id, ranking_text, prompt_text, ranking_file,
                prompt_file, poster_file, poster_status, email_status,
                created_at, updated_at
            ) VALUES (22, 999, '', '', '', '', '', '', '', '', '')
            """
        )
    output = tmp_path / "migrated.db"

    with pytest.raises(migrations.MigrationError, match="孤儿 Report"):
        migrations.migrate_database(source, output)

    assert not output.exists()
    assert not output.with_suffix(".db.manifest.json").exists()


def test_migration_rejects_missing_parent_run(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    with sqlite3.connect(source) as connection:
        connection.execute(
            """
            INSERT INTO group_runs(
                id, run_id, group_id, provider_used, message_count,
                speaker_count, ranking_status, prompt_status, error_message
            ) VALUES (12, 999, 1, '', 0, 0, 'failed', 'skipped', '')
            """
        )

    with pytest.raises(migrations.MigrationError, match="缺失父 Run"):
        migrations.preflight_database(source)


def test_migration_rejects_an_already_migrated_source(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    migrated = tmp_path / "migrated.db"
    migrations.migrate_database(source, migrated)

    with pytest.raises(migrations.MigrationError, match="已经执行"):
        migrations.migrate_database(migrated, tmp_path / "second.db")


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_preflight_rejects_sqlite_sidecars(tmp_path: Path, suffix: str) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    sidecar = Path(f"{source}{suffix}")
    sidecar.write_bytes(b"writer evidence")

    with pytest.raises(migrations.MigrationError, match="不能视为离线源"):
        migrations.preflight_database(source)


def test_preflight_rejects_unknown_user_version(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 7")

    with pytest.raises(migrations.MigrationError, match="user_version=7"):
        migrations.preflight_database(source)


def test_preflight_rejects_unknown_columns_on_rebuilt_tables(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    with sqlite3.connect(source) as connection:
        connection.execute("ALTER TABLE group_runs ADD COLUMN future_data TEXT")

    with pytest.raises(migrations.MigrationError, match="未知列"):
        migrations.preflight_database(source)


def test_preflight_rejects_unknown_dependent_schema_objects(tmp_path: Path) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TRIGGER future_trigger AFTER INSERT ON reports BEGIN SELECT 1; END"
        )

    with pytest.raises(migrations.MigrationError, match="未知触发器"):
        migrations.preflight_database(source)


def test_failed_migration_removes_only_its_temporary_files(tmp_path: Path, monkeypatch) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    output = tmp_path / "migrated.db"
    source_hash = _hash(source)

    def fail_after_backup(_database: Path) -> None:
        raise sqlite3.OperationalError("injected failure")

    monkeypatch.setattr(migrations, "_apply_relationship_migration", fail_after_backup)

    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        migrations.migrate_database(source, output)

    assert _hash(source) == source_hash
    assert not output.exists()
    assert not output.with_suffix(".db.manifest.json").exists()
    assert list(tmp_path.glob(".*.tmp")) == []


def test_cli_requires_explicit_action_and_supports_dry_run(tmp_path: Path, capsys) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    output = tmp_path / "planned.db"

    exit_code = migrations.main(
        ["--source", str(source), "--output", str(output), "--dry-run"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["planned_output"] == str(output.resolve())
    assert not output.exists()


def test_cli_dry_run_rejects_an_existing_output(tmp_path: Path, capsys) -> None:
    source = _create_legacy_database(tmp_path / "legacy.db")
    output = tmp_path / "existing.db"
    output.write_bytes(b"keep me")

    exit_code = migrations.main(
        ["--source", str(source), "--output", str(output), "--dry-run"]
    )

    assert exit_code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "拒绝覆盖" in payload["error"]
    assert output.read_bytes() == b"keep me"
