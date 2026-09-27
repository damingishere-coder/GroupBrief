from pathlib import Path
import json
import sqlite3

import pytest

from scripts.configure_china_workdays import configure, TARGETS, OVERRIDE


@pytest.fixture
def migration(tmp_path):
    database = tmp_path / "groups.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE groups(id INTEGER PRIMARY KEY, display_name TEXT, wechat_group_name TEXT, wechat_group_id TEXT, enabled INTEGER, deleted_at TEXT, schedule_rule TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        for group_id, chat_id in {**TARGETS, 31: "disabled@chatroom"}.items():
            conn.execute("INSERT INTO groups VALUES (?,?,?,?,?,NULL,?,'2026-09-27T00:00:00')", (group_id, f"群{group_id}", f"群{group_id}", chat_id, int(group_id != 31), "workdays_daily_monday_weekly"))
        conn.executemany("INSERT INTO settings VALUES (?,?)", [("schedule_generate_time", "00:15"), ("schedule_send_time", "10:00")])
    return database, tmp_path / "calendar", tmp_path / "output"


def test_preview_apply_backup_and_idempotency(migration):
    database, calendar, output = migration
    before = database.read_bytes()
    assert not configure(*migration)["applied"]
    assert database.read_bytes() == before and not calendar.exists() and not output.exists()
    result = configure(*migration, apply=True)
    assert result["applied"] and Path(result["backup"]).is_dir()
    manifest_path = output / ".scheduler" / "2026-09-28.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["expected_groups"]
    assert len(manifest) == 6 and all(row["period_start"] == "2026-09-25T00:00:00" and row["calendar_version"] for row in manifest)
    frozen = manifest_path.read_bytes()
    assert configure(*migration, apply=True)["already_applied"]
    assert manifest_path.read_bytes() == frozen
    with sqlite3.connect(database) as conn:
        assert conn.execute("select schedule_rule from groups where id=31").fetchone()[0] == "workdays_daily_monday_weekly"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_rolls_back_files_and_database(migration, monkeypatch):
    import scripts.configure_china_workdays as module
    def fail(*args, **kwargs):
        raise ValueError("injected failure")
    monkeypatch.setattr(module, "build_expected_groups", fail)
    with pytest.raises(ValueError, match="injected"):
        configure(*migration, apply=True)
    database, calendar, output = migration
    assert not (calendar / "overrides.json").exists()
    assert not (output / ".scheduler" / "2026-09-28.json").exists()
    with sqlite3.connect(database) as conn:
        assert conn.execute("select count(*) from groups where schedule_rule='china_workdays'").fetchone()[0] == 0


def test_migration_refuses_conflicting_task(migration):
    database, calendar, output = migration
    state = output / ".scheduler" / "2026-09-28.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"run_date": "2026-09-28", "expected_groups": []}), encoding="utf-8")
    before = state.read_bytes()
    with pytest.raises(ValueError, match="已有不同任务清单"):
        configure(*migration, apply=True)
    assert state.read_bytes() == before
