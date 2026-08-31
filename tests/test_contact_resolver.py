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


def _varint(value: int) -> bytes:
    result = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        result.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(result)


def _field(number: int, value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return _varint((number << 3) | 2) + _varint(len(raw)) + raw


def _member(*fields: tuple[int, str]) -> bytes:
    return _field(1, b"".join(_field(number, value) for number, value in fields))


def _add_chat_room(db: str, chatroom: str, ext_buffer: bytes) -> None:
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE chat_room (username TEXT, ext_buffer BLOB)")
    con.execute(
        "INSERT INTO chat_room (username, ext_buffer) VALUES (?, ?)",
        (chatroom, ext_buffer),
    )
    con.commit()
    con.close()


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


def test_group_card_field4_inviter_does_not_leak_to_member_without_card(tmp_path):
    db = _make_db(
        tmp_path,
        [("jiangzhema123", "春夏秋冬", ""), ("to1900", "", "罗斯")],
    )
    ext_buffer = _member(
        (1, "jiangzhema123"), (2, "鲁布斯"), (4, "to1900")
    ) + _member((1, "to1900"), (4, "wxid_inviter"))
    _add_chat_room(db, "tea@chatroom", ext_buffer)

    resolver = ContactResolver(db)

    assert resolver.group_nicknames(
        "tea@chatroom", ["jiangzhema123", "to1900"]
    ) == {"jiangzhema123": "鲁布斯"}


def test_group_card_owner_field_does_not_collapse_large_group(tmp_path):
    db = _make_db(tmp_path, [("member_a123", "成员甲", ""), ("member_b123", "成员乙", "")])
    ext_buffer = _member(
        (1, "member_a123"), (2, "群名片甲"), (4, "c2341298")
    ) + _member((1, "member_b123"), (4, "c2341298"))
    _add_chat_room(db, "grok@chatroom", ext_buffer)

    resolver = ContactResolver(db)

    assert resolver.group_nicknames(
        "grok@chatroom", ["member_a123", "member_b123", "c2341298"]
    ) == {"member_a123": "群名片甲"}


def test_group_card_preserves_short_and_long_values_exactly(tmp_path):
    db = _make_db(tmp_path, [])
    ext_buffer = _member((1, "member_short"), (2, "广州")) + _member(
        (1, "member_long"), (2, "广州-U啥都行-好好上 b 班版")
    )
    _add_chat_room(db, "eason@chatroom", ext_buffer)

    resolver = ContactResolver(db)

    assert resolver.group_nicknames(
        "eason@chatroom", ["member_short", "member_long"]
    ) == {
        "member_short": "广州",
        "member_long": "广州-U啥都行-好好上 b 班版",
    }


def test_group_card_rejects_system_event_text_in_field2(tmp_path):
    db = _make_db(tmp_path, [("member_event", "联系人名称", "")])
    _add_chat_room(
        db,
        "dirty@chatroom",
        _member((1, "member_event"), (2, "群主邀请了“景甜”进入群聊")),
    )

    resolver = ContactResolver(db)

    assert resolver.group_nicknames("dirty@chatroom", ["member_event"]) == {}


def test_group_card_keeps_legacy_field4_member_layout(tmp_path):
    db = _make_db(tmp_path, [])
    _add_chat_room(
        db,
        "legacy@chatroom",
        _member((4, "legacy_member"), (1, "旧布局群名片")),
    )

    resolver = ContactResolver(db)

    assert resolver.group_nicknames("legacy@chatroom", ["legacy_member"]) == {
        "legacy_member": "旧布局群名片"
    }
