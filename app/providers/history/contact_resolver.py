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
import sqlite3
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("groupbrief.contact")


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
