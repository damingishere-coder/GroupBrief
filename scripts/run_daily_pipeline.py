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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.outcome import outcome_for_status, summarize_results


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


def _print_outcome(outcome: dict) -> int:
    audit = {
        "outcome_status": outcome["outcome_status"],
        "exit_code": int(outcome["exit_code"]),
        "result_count": int(outcome.get("result_count") or 0),
        "source_statuses": outcome.get("source_statuses") or [],
    }
    print("OUTCOME " + json.dumps(audit, ensure_ascii=False, sort_keys=True))
    return audit["exit_code"]


def _finish_results(results: list[dict]) -> int:
    _print_results(results)
    return _print_outcome(summarize_results(results))


def _execute(args, pipeline: DailyPipeline) -> int:
    if args.cmd == "status":
        runs = pipeline.store.list_runs()
        print(f"共 {len(runs)} 个运行记录：")
        for run in runs[:20]:
            print(
                f"  {run.get('run_date')} | {run.get('group_name')} | "
                f"{run.get('status')} | {run.get('updated_at')}"
            )
        if any(str(run.get("status") or "").upper() == "CORRUPT" for run in runs):
            return _print_outcome(outcome_for_status("blocked"))
        return _print_outcome(outcome_for_status("success" if runs else "not_run"))

    if args.cmd == "generate":
        return _finish_results(
            pipeline.generate_all(
                run_date=args.date,
                group_ids=args.group,
                refresh_messages=args.refresh_messages,
            )
        )
    if args.cmd == "send":
        return _finish_results(pipeline.send_due())
    if args.cmd == "force-generate":
        return _finish_results(
            [
                pipeline.force_generate(
                    args.group,
                    args.date,
                    refresh_messages=args.refresh_messages,
                )
            ]
        )
    if args.cmd == "rebuild-prompt":
        return _finish_results([pipeline.rebuild_prompt_from_snapshot(args.group, args.date)])
    if args.cmd == "force-send":
        return _finish_results([pipeline.force_send(args.group, args.date)])
    return _print_outcome(outcome_for_status("failed"))


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
        return _print_outcome(outcome_for_status("failed"))

    try:
        pipeline = _pipeline(
            dry_run=True if args.cmd == "status" else bool(getattr(args, "dry_run", False))
        )
    except Exception as exc:
        return _finish_results(
            [
                {
                    "status": "failed",
                    "detail": f"初始化失败：{type(exc).__name__}: {str(exc)[:240]}",
                }
            ]
        )

    try:
        return _execute(args, pipeline)
    except Exception as exc:
        return _finish_results(
            [
                {
                    "status": "failed",
                    "detail": f"执行失败：{type(exc).__name__}: {str(exc)[:240]}",
                }
            ]
        )


if __name__ == "__main__":
    raise SystemExit(main())
