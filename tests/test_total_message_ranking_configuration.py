import sqlite3

import pytest

from scripts.configure_total_message_ranking import configure


def make_database(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE groups (
            id INTEGER PRIMARY KEY, display_name TEXT, enabled BOOLEAN,
            deleted_at TEXT, ranking_count_policy TEXT, ranking_template TEXT,
            sender_name_policy TEXT, wechat_send_enabled BOOLEAN)""")
        for group_id in range(23, 30):
            conn.execute(
                "INSERT INTO groups VALUES (?, ?, ?, NULL, ?, ?, ?, 0)",
                (group_id, f"群{group_id}", group_id < 29,
                 "text_primary_with_interactions", "text_interactions",
                 "wechat_data_analysis"),
            )


def test_preview_is_readonly_and_apply_preserves_other_fields_and_backup(tmp_path):
    path = tmp_path / "groups.db"
    make_database(path)
    original = path.read_bytes()
    assert configure(path)["applied"] is False
    assert path.read_bytes() == original
    result = configure(path, apply=True)
    assert result["applied"] is True
    with sqlite3.connect(result["backup"]) as backup:
        assert "strict_image_fact_check" not in {
            row[1] for row in backup.execute("PRAGMA table_info(groups)")
        }
        assert backup.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 7
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM groups WHERE ranking_count_policy='all_messages' "
            "AND ranking_template='default' AND strict_image_fact_check=1"
        ).fetchone()[0] == 6
        assert conn.execute("SELECT wechat_send_enabled FROM groups").fetchall() == [(0,)] * 7
        assert conn.execute(
            "SELECT ranking_count_policy, strict_image_fact_check FROM groups WHERE id=29"
        ).fetchone() == ("text_primary_with_interactions", 0)
    assert configure(path, apply=True)["applied"] is True


def test_unexpected_active_group_is_rejected_without_writes(tmp_path):
    path = tmp_path / "groups.db"
    make_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE groups SET enabled=1 WHERE id=29")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="活动群已变化"):
        configure(path, apply=True)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.ranking-backup-*"))
