"""六群周期切换工具。默认只预览；生产批准后才使用 --apply。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

RULE = "workdays_daily_monday_weekly"
TARGETS = {
    23: "48643066777@chatroom", 24: "54409439719@chatroom",
    25: "45638125048@chatroom", 26: "44726152571@chatroom",
    27: "43058033720@chatroom", 28: "9439243003@chatroom",
}


def configure(database: Path, *, apply: bool = False) -> dict:
    database = database.resolve(strict=True)
    with sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(
            "SELECT id, wechat_group_id, display_name, enabled, schedule_rule FROM groups WHERE id IN (23,24,25,26,27,28) ORDER BY id"
        )]
        if len(rows) != len(TARGETS) or any(not row["enabled"] or TARGETS[row["id"]] != row["wechat_group_id"] for row in rows):
            raise ValueError("六群身份或启用状态与已审阅的目标不一致，停止切换")
        result = {"applied": False, "database": str(database), "groups": rows, "new_rule": RULE}
        if not apply or all(row["schedule_rule"] == RULE for row in rows):
            return result
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.parent / "backups" / f"{database.stem}-before-workdays-weekly-{stamp}.db"
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as copy:
            connection.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("备份完整性检查失败，未修改配置")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                cursor = connection.execute(
                    "UPDATE groups SET schedule_rule=?, updated_at=? WHERE id=? AND wechat_group_id=? AND enabled=1 AND schedule_rule=?",
                    (RULE, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), row["id"], row["wechat_group_id"], row["schedule_rule"]),
                )
                if cursor.rowcount != 1:
                    raise ValueError("预览后群配置发生变化，已回滚")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("切换后数据库校验失败，已回滚")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        result.update(applied=True, backup=str(backup))
        return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="仅在已批准生产切换后使用；先备份再事务更新")
    args = parser.parse_args()
    print(json.dumps(configure(args.database, apply=args.apply), ensure_ascii=False, indent=2))
