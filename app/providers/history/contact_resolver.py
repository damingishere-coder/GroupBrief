"""联系人解析器：把微信「微信号」解析成用户认识的「显示名」。

上游 WeChatDataAnalysis MCP 返回的 senderDisplayName 不可靠（可能把
微信号、错误昵称当成显示名）。而微信解密数据库 contact.db 里的
`username → remark/nick_name` 是权威映射：

- 备注（remark）优先：用户给联系人起的名字，最符合用户认知；
- 其次昵称（nick_name）：联系人当前微信昵称；
- 都没有则保留原样（微信号/空）。

contact.db 由 WeChatDataAnalysis 在启动时解密导出，位置默认自动探测：
`%APPDATA%/wechat-data-analysis-desktop/output/databases/<账号>/contact.db`；
也可通过设置 wechat_contact_db_path 显式指定。
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Iterator

from app.core.logging import get_logger

logger = get_logger("groupbrief.contact")

_SYSTEM_DISPLAY_NAME_MARKERS = (
    "加入群聊",
    "进入群聊",
    "退出群聊",
    "移出群聊",
    "邀请了",
    "修改群名",
    "修改了群名",
    "红包待领取",
    "撤回了一条消息",
    "拍了拍",
)


def is_plausible_group_card(value: object) -> bool:
    """排除 ext_buffer 中混入的系统事件文本和结构性脏值。"""
    text = str(value or "").strip()
    return bool(
        text
        and len(text) <= 64
        and "\n" not in text
        and "\r" not in text
        and not any(marker in text for marker in _SYSTEM_DISPLAY_NAME_MARKERS)
    )


def _decode_varint(raw: bytes, offset: int) -> tuple[int | None, int]:
    value = 0
    shift = 0
    position = int(offset)
    while position < len(raw):
        byte = raw[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, position
        shift += 7
        if shift > 63:
            break
    return None, len(raw)


def _iter_protobuf_fields(raw: bytes) -> Iterator[tuple[int, int, bytes]]:
    """遍历群成员 ext_buffer 中的 length-delimited protobuf 字段。"""
    position = 0
    while position < len(raw):
        tag, next_position = _decode_varint(raw, position)
        if tag is None or next_position <= position:
            break
        position = next_position
        field_number = int(tag) >> 3
        wire_type = int(tag) & 0x07
        if wire_type == 0:
            _, next_position = _decode_varint(raw, position)
            if next_position <= position:
                break
            position = next_position
            continue
        if wire_type == 1:
            position += 8
            continue
        if wire_type == 5:
            position += 4
            continue
        if wire_type != 2:
            break
        size, next_position = _decode_varint(raw, position)
        if size is None or next_position <= position:
            break
        position = next_position
        end = position + int(size)
        if end > len(raw):
            break
        yield field_number, wire_type, raw[position:end]
        position = end


def _looks_like_username(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("wxid_", "gh_")) or text.endswith("@chatroom") or "@" in text:
        return True
    return bool(
        6 <= len(text) <= 32
        and not re.search(r"\s", text)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]+", text)
    )


def _pick_legacy_group_card(fields: list[tuple[int, str]], username: str) -> str:
    """兼容旧布局：field 4 是成员 ID、field 1 是显示名。"""
    candidates: list[tuple[int, int, str]] = []
    for index, (field_number, value) in enumerate(fields):
        text = str(value or "").strip()
        if not text or text == username or len(text) > 64 or "\n" in text or "\r" in text:
            continue
        if text.startswith(("wxid_", "gh_")) or text.endswith("@chatroom") or "@" in text:
            continue
        score = (100 if field_number == 2 else 0) + (20 if not _looks_like_username(text) else 0)
        score += max(0, 32 - len(text))
        candidates.append((score, -index, text))
    return max(candidates, default=(-1, 0, ""))[2]


def _parse_group_nicknames(ext_buffer: bytes, usernames: set[str]) -> dict[str, str]:
    """按当前字段语义解析群名片，并保留受限的旧布局兼容。"""
    result: dict[str, str] = {}
    primary_seen: set[str] = set()
    for _, wire_type, chunk in _iter_protobuf_fields(ext_buffer):
        if wire_type != 2 or not chunk:
            continue
        text_fields: list[tuple[int, str]] = []
        for field_number, nested_wire_type, value in _iter_protobuf_fields(chunk):
            if nested_wire_type != 2 or not value or len(value) > 256:
                continue
            try:
                text = bytes(value).decode("utf-8", errors="strict").strip()
            except UnicodeDecodeError:
                continue
            if text:
                text_fields.append((field_number, text))
        if not text_fields:
            continue

        field1 = [value for field_number, value in text_fields if field_number == 1]
        field2 = [value for field_number, value in text_fields if field_number == 2]
        primary_members = [value for value in field1 if value in usernames]
        if primary_members:
            for username in primary_members:
                primary_seen.add(username)
                if field2 and is_plausible_group_card(field2[0]):
                    result[username] = field2[0]
                else:
                    result.pop(username, None)
            continue

        legacy_members = [
            value
            for field_number, value in text_fields
            if field_number == 4 and value in usernames and value not in primary_seen
        ]
        for username in legacy_members:
            display = _pick_legacy_group_card(text_fields, username)
            if display:
                result[username] = display
    return result


def find_contact_db() -> Path | None:
    """自动探测 WeChatDataAnalysis 解密的联系人数据库。"""
    if os.environ.get("GROUPBRIEF_NO_CONTACT_DB") == "1":
        return None  # 测试隔离：不读取真实微信联系人
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    base = Path(appdata) / "wechat-data-analysis-desktop" / "output" / "databases"
    if not base.is_dir():
        return None
    for acct_dir in sorted(base.iterdir()):
        if not acct_dir.is_dir():
            continue
        db = acct_dir / "contact.db"
        if db.is_file():
            return db
    return None


class ContactResolver:
    """微信号 → 显示名 映射（备注优先，其次昵称）。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path: Path | None = None
        if db_path:
            self._db_path = Path(db_path)
        else:
            try:
                self._db_path = find_contact_db()
            except Exception:
                self._db_path = None
        self._map: dict[str, str] = {}
        self._loaded = False

    @property
    def available(self) -> bool:
        return self._db_path is not None and self._db_path.is_file()

    def load(self) -> dict[str, str]:
        """读取联系人表，返回 {微信号: 显示名}。失败时返回空映射。"""
        if self._loaded:
            return self._map
        self._loaded = True
        if not self.available:
            return self._map
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT username, remark, nick_name FROM contact"
                ).fetchall()
                for row in rows:
                    username = (row["username"] or "").strip()
                    if not username:
                        continue
                    remark = (row["remark"] or "").strip()
                    nick = (row["nick_name"] or "").strip()
                    name = remark or nick
                    if name:
                        self._map[username] = name
            finally:
                con.close()
            logger.info("已加载联系人映射 %d 条（%s）", len(self._map), self._db_path)
        except Exception as e:  # 防御：DB 被占用/损坏时不影响读取
            logger.warning("读取联系人映射失败：%s", str(e)[:200])
        return self._map

    def display_name(self, username: str) -> str | None:
        """解析微信号对应的显示名；无映射返回 None。"""
        if not username:
            return None
        if not self._loaded:
            self.load()
        return self._map.get(username)

    def resolve_name(self, username: str, fallback: str = "") -> str:
        """解析微信号对应的显示名，找不到时回退到 fallback。"""
        name = self.display_name(username)
        return name if name else fallback

    def group_nicknames(self, chatroom_id: str, usernames: list[str]) -> dict[str, str]:
        """只读解析指定群的成员群名片；任何读取或格式异常均安全回退为空。"""
        chatroom = str(chatroom_id or "").strip()
        targets = {
            str(username or "").strip()
            for username in usernames
            if str(username or "").strip()
        }
        if not chatroom.endswith("@chatroom") or not targets or not self.available:
            return {}
        try:
            con = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT ext_buffer FROM chat_room WHERE username = ? LIMIT 1",
                    (chatroom,),
                ).fetchone()
            finally:
                con.close()
            if row is None or row[0] is None:
                return {}
            raw = row[0].tobytes() if isinstance(row[0], memoryview) else bytes(row[0])
            return _parse_group_nicknames(raw, targets) if raw else {}
        except Exception as exc:
            logger.warning("读取群名片映射失败：%s", str(exc)[:200])
            return {}
