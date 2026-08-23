"""GroupBrief V2 统一入口：每日全流程 Pipeline。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py generate            # 生成阶段（今天）
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py generate --date 2026-08-17
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py generate --dry-run  # 发送用 dry_run
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py send [--dry-run]    # 发送到点群
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py force-generate --group 1
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py rebuild-prompt --group 1 --date 2026-08-23
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py force-send --group 1
    .venv\\Scripts\\python.exe scripts/run_daily_pipeline.py status              # 最近运行状态
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline


def _pipeline(dry_run: bool = False) -> DailyPipeline:
    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)
    return DailyPipeline(dry_run=dry_run)


def _print_results(results) -> None:
    for r in results:
        status = r.get("status", "?")
        detail = r.get("detail") or r.get("reason") or r.get("error") or ""
        print(f"  [{status}] {r.get('group_name', '')} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief V2 每日流水线")
    sub = parser.add_subparsers(dest="cmd")

    p_gen = sub.add_parser("generate", help="生成阶段")
    p_gen.add_argument("--date", help="运行日期 YYYY-MM-DD（默认今天）")
    p_gen.add_argument("--dry-run", action="store_true", help="发送使用 dry_run")
    p_gen.add_argument("--group", type=int, action="append", help="仅处理指定群（可多次）")
    p_gen.add_argument(
        "--refresh-messages",
        action="store_true",
        help="只重新读取并覆盖当天 messages.json；不重建 Prompt、不生图",
    )

    p_send = sub.add_parser("send", help="发送到点群")
    p_send.add_argument("--dry-run", action="store_true")

    p_fg = sub.add_parser("force-generate", help="强制重新生成指定群")
    p_fg.add_argument("--group", type=int, required=True)
    p_fg.add_argument("--date", help="运行日期（默认今天）")
    p_fg.add_argument(
        "--refresh-messages",
        action="store_true",
        help="只重新读取并覆盖当天 messages.json；不重建 Prompt、不生图",
    )

    p_rp = sub.add_parser("rebuild-prompt", help="只从当天 messages.json 重建排行榜和 Prompt")
    p_rp.add_argument("--group", type=int, required=True)
    p_rp.add_argument("--date", required=True, help="运行日期 YYYY-MM-DD")

    p_fs = sub.add_parser("force-send", help="强制发送指定群")
    p_fs.add_argument("--group", type=int, required=True)
    p_fs.add_argument("--date", help="运行日期（默认今天）")

    sub.add_parser("status", help="最近运行状态")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "status":
        pipeline = _pipeline(dry_run=True)
        runs = pipeline.store.list_runs()
        print(f"共 {len(runs)} 个运行记录：")
        for r in runs[:20]:
            print(f"  {r.get('run_date')} | {r.get('group_name')} | {r.get('status')} | {r.get('updated_at')}")
        return 0

    pipeline = _pipeline(dry_run=bool(getattr(args, "dry_run", False)))

    if args.cmd == "generate":
        results = pipeline.generate_all(
            run_date=args.date,
            group_ids=args.group,
            refresh_messages=args.refresh_messages,
        )
        _print_results(results)
        return 0

    if args.cmd == "send":
        results = pipeline.send_due()
        _print_results(results)
        return 0

    if args.cmd == "force-generate":
        r = pipeline.force_generate(
            args.group,
            args.date,
            refresh_messages=args.refresh_messages,
        )
        _print_results([r])
        return 0

    if args.cmd == "rebuild-prompt":
        r = pipeline.rebuild_prompt_from_snapshot(args.group, args.date)
        _print_results([r])
        return 0

    if args.cmd == "force-send":
        r = pipeline.force_send(args.group, args.date)
        _print_results([r])
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
