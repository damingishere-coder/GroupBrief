"""ContactResolver 测试：微信号 → 显示名（备注优先、其次昵称）。"""

from __future__ import annotations

import sqlite3

from app.providers.history.contact_resolver import ContactResolver


def _make_db(tmp_path, rows: list[tuple[str, str, str]]) -> str:
    """构造临时联系人库：rows = (username, remark, nick_name)。"""
    db = tmp_path / "contact.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE contact (username TEXT, remark TEXT, nick_name TEXT)"
    )
    con.executemany(
        "INSERT INTO contact (username, remark, nick_name) VALUES (?,?,?)", rows
    )
    con.commit()
    con.close()
    return str(db)


def test_prefers_remark_over_nickname(tmp_path):
    db = _make_db(tmp_path, [("wxid_a", "备注名", "昵称名")])
    r = ContactResolver(db)
    assert r.resolve_name("wxid_a") == "备注名"


def test_falls_back_to_nickname(tmp_path):
    db = _make_db(tmp_path, [("wxid_b", "", "真实昵称")])
    r = ContactResolver(db)
    assert r.resolve_name("wxid_b") == "真实昵称"


def test_no_mapping_keeps_fallback(tmp_path):
    db = _make_db(tmp_path, [])
    r = ContactResolver(db)
    assert r.resolve_name("wxid_zzz", "原始名") == "原始名"
    assert r.display_name("wxid_zzz") is None


def test_empty_username_skipped(tmp_path):
    db = _make_db(tmp_path, [("", "备注", "昵称"), ("  ", "备注2", "昵称2")])
    r = ContactResolver(db)
    assert len(r.load()) == 0


def test_missing_db_file(tmp_path):
    r = ContactResolver(tmp_path / "not_exist.db")
    assert r.available is False
    assert r.resolve_name("wxid_x", "原名") == "原名"


def test_blank_names_skipped(tmp_path):
    db = _make_db(tmp_path, [("wxid_c", "", ""), ("wxid_d", "有备注", "")])
    r = ContactResolver(db)
    m = r.load()
    assert "wxid_c" not in m
    assert m["wxid_d"] == "有备注"
