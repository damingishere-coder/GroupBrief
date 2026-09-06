"""六群切换总消息排行。默认只读；--apply 仅可在正式服务停止后使用。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import sqlite3
import sys

GROUP_IDS = (23, 24, 25, 26, 27, 28)


def inspect_groups(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM groups WHERE enabled = 1 AND deleted_at IS NULL ORDER BY id"
    )]
    if tuple(row["id"] for row in rows) != GROUP_IDS:
        raise ValueError("活动群已变化：必须重新核对目标，当前脚本只接受群 23–28")
    for row in rows:
        old = (row["ranking_count_policy"], row["ranking_template"])
        if old not in {
            ("text_primary_with_interactions", "text_interactions"),
            ("all_messages", "default"),
        } or row["sender_name_policy"] != "wechat_data_analysis":
            raise ValueError(f"群 {row['id']} 配置与预期不符，停止更新")
    return rows


def check_database(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("数据库完整性校验失败")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("数据库外键校验失败")


def configure(database: Path, *, apply: bool = False) -> dict:
    database = database.resolve(strict=True)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
        check_database(source)
        before = inspect_groups(source)
        preview = [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "before": {key: row.get(key, False) for key in (
                    "ranking_count_policy", "ranking_template", "strict_image_fact_check"
                )},
                "after": {
                    "ranking_count_policy": "all_messages",
                    "ranking_template": "default",
                    "strict_image_fact_check": True,
                },
            }
            for row in before
        ]
        if not apply:
            return {"applied": False, "database": str(database), "groups": preview}
        backup = database.with_name(
            database.name + ".ranking-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        )
        with sqlite3.connect(backup) as target:
            source.backup(target)
            check_database(target)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        if inspect_groups(connection) != before:
            raise ValueError("备份后配置发生变化，停止更新")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(groups)")}
        if "strict_image_fact_check" not in columns:
            connection.execute(
                "ALTER TABLE groups ADD COLUMN strict_image_fact_check BOOLEAN NOT NULL DEFAULT 0"
            )
        connection.execute(
            "UPDATE groups SET ranking_count_policy='all_messages', "
            "ranking_template='default', strict_image_fact_check=1 "
            "WHERE id IN (23,24,25,26,27,28)"
        )
        after = inspect_groups(connection)
        for old, new in zip(before, after):
            expected = {
                **old, "ranking_count_policy": "all_messages",
                "ranking_template": "default", "strict_image_fact_check": 1,
            }
            if new != expected:
                raise ValueError("更新后的群配置不符合预期")
        check_database(connection)
    return {"applied": True, "database": str(database), "backup": str(backup), "groups": preview}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--service-stopped", action="store_true")
    args = parser.parse_args()
    if args.apply:
        if not args.service_stopped:
            parser.error("应用前须授权并停止正式服务，再加 --service-stopped")
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(("127.0.0.1", 8766)) == 0:
                parser.error("8766 仍在监听，拒绝在正式服务运行时更新")
    print(json.dumps(configure(args.database, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
