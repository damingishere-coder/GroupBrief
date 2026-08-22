"""GroupBrief V2 P1 验证脚本：WeChatDataAnalysis 数据源真实验证。

只读取数据，绝不修改微信数据。测试结果保存到 output/test-data/。

用法（在项目根目录运行）：
    .venv\\Scripts\\python.exe scripts/test_wechat_data.py health
    .venv\\Scripts\\python.exe scripts/test_wechat_data.py list-groups
    .venv\\Scripts\\python.exe scripts/test_wechat_data.py resolve "示例"
    .venv\\Scripts\\python.exe scripts/test_wechat_data.py fetch <group_id> --start 2026-08-17 --end 2026-08-17
    .venv\\Scripts\\python.exe scripts/test_wechat_data.py all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, time
from pathlib import Path

# 让脚本可直接从项目根目录导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.db import repository as repo


def _load_settings():
    """模拟 app 启动：初始化 DB 并应用数据库设置（数据库优先于 .env）。"""
    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)
    return settings


def _out_dir() -> Path:
    base = get_settings().output_dir / "test-data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save(name: str, data) -> Path:
    path = _out_dir() / name
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"  → 已保存 {path}")
    return path


def _safe(text: str) -> str:
    return "".join(c for c in text if c.isalnum() or c in "-_")


def cmd_health(source) -> dict:
    print("== health_check ==")
    h = source.health_check()
    print(f"  状态: {h.status.value}")
    print(f"  详情: {h.detail}")
    return {"status": h.status.value, "detail": h.detail}


def cmd_list_groups(source) -> dict:
    print("== list_groups ==")
    groups = source.list_groups()
    print(f"  发现 {len(groups)} 个群")
    for g in groups[:10]:
        print(f"    {g.group_id} | {g.group_name} | 成员 {g.member_count}")
    if len(groups) > 10:
        print(f"    …（其余 {len(groups) - 10} 个省略）")
    return [{"group_id": g.group_id, "group_name": g.group_name, "member_count": g.member_count} for g in groups]


def cmd_resolve(source, name: str) -> dict:
    print(f"== resolve_group({name!r}) ==")
    candidates = source.resolve_group(name)
    print(f"  匹配 {len(candidates)} 个候选")
    for c in candidates:
        print(f"    {c.group_id} | {c.group_name}")
    return [{"group_id": c.group_id, "group_name": c.group_name, "member_count": c.member_count} for c in candidates]


def cmd_fetch(source, group_id: str, start: str, end: str) -> dict:
    start_dt = datetime.combine(datetime.fromisoformat(start).date(), time.min)
    end_dt = datetime.combine(datetime.fromisoformat(end).date(), time.max)
    print(f"== fetch_messages({group_id}, {start} 00:00:00 ~ {end} 23:59:59) ==")
    result = source.fetch_messages(group_id, start_dt, end_dt)
    print(f"  状态: {result.status.value}")
    print(f"  错误类型: {result.error_type or '(无)'}")
    print(f"  详情: {result.detail}")
    print(f"  消息数: {len(result.messages)}")

    data = {
        "group_id": group_id,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "status": result.status.value,
        "error_type": result.error_type or "",
        "detail": result.detail,
        "message_count": len(result.messages),
        "messages": [m.to_dict() for m in result.messages],
    }
    if result.messages:
        _save(f"messages_{_safe(group_id)}_{start}.json", data)
        sample = result.messages[0].to_dict()
        print("  首条消息字段: " + json.dumps(sample, ensure_ascii=False)[:300])
        speakers = {}
        for m in result.messages:
            speakers[m.sender_name] = speakers.get(m.sender_name, 0) + 1
        top = sorted(speakers.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        print(f"  发言人数: {len(speakers)}，Top5: {top}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief V2 WeChatDataAnalysis 数据源验证")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("health", help="健康检查")
    sub.add_parser("list-groups", help="列出群")
    p_resolve = sub.add_parser("resolve", help="解析群名")
    p_resolve.add_argument("name", help="群名关键词")
    p_fetch = sub.add_parser("fetch", help="按时间段取消息")
    p_fetch.add_argument("group_id", help="群 ID（如 xxx@chatroom）")
    p_fetch.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    p_fetch.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    sub.add_parser("all", help="全部：health + list + resolve + fetch 首群")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    settings = _load_settings()
    source = WeChatDataAnalysisSource(settings=settings)

    summary = {}

    if args.cmd in ("health", "all"):
        summary["health"] = cmd_health(source)
        _save("health.json", summary["health"])

    if args.cmd in ("list-groups", "all"):
        summary["groups"] = cmd_list_groups(source)
        _save("groups.json", summary["groups"])

    if args.cmd == "resolve":
        summary["resolve"] = cmd_resolve(source, args.name)
        _save(f"resolve_{_safe(args.name)}.json", summary["resolve"])

    if args.cmd == "fetch":
        summary["fetch"] = cmd_fetch(source, args.group_id, args.start, args.end)

    if args.cmd == "all":
        # 使用显式环境变量中的测试关键词，避免在公共脚本里固化真实群名。
        from datetime import timedelta

        today = datetime.now().date()
        query = os.environ.get("GROUPBRIEF_TEST_GROUP_QUERY", "示例").strip() or "示例"
        candidates = source.resolve_group(query)
        summary["all_resolve"] = candidates[:5]
        _save(
            f"resolve_{_safe(query)}.json",
            [{"group_id": c.group_id, "group_name": c.group_name} for c in candidates[:5]],
        )
        if candidates:
            g = candidates[0]
            yesterday = today - timedelta(days=1)
            summary["all_fetch"] = cmd_fetch(source, g.group_id, yesterday.isoformat(), yesterday.isoformat())

    _save("summary.json", summary)
    print("== 完成，结果已保存到 output/test-data/ ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
