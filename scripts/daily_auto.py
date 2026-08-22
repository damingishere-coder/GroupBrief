"""GroupBrief V2 每日自动任务：生成前一天报告 + 发送邮件。

由 Windows 计划任务每天 00:15 触发（见 install_daily_task.py）。

逻辑（与用户约定一致）：
    每天 00:15 到达「新的一天」，run_date = 今天，
    统计窗口自动为「昨天 00:00:00 ~ 23:59:59」。
    例：8/22 00:15 触发 → run_date=2026-08-22 → 统计 2026-08-21 全天。

用法（手动测试）：
    .venv\\Scripts\\python.exe scripts/daily_auto.py                     # 默认今天
    .venv\\Scripts\\python.exe scripts/daily_auto.py --date 2026-08-21   # 指定执行日
    .venv\\Scripts\\python.exe scripts/daily_auto.py --skip-email        # 只生成不发邮件

周一至周日均执行；每天只统计前一自然日，不再合并周末。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.scheduler.daily_v2_job import run_daily_v2_job

LOG_DIR = PROJECT_ROOT / "output" / "logs"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"daily_auto_{datetime.now():%Y-%m-%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief V2 每日自动任务（生成+发邮件）")
    parser.add_argument("--date", help="执行日 YYYY-MM-DD（默认今天；统计前一天）")
    parser.add_argument("--skip-email", action="store_true", help="只生成不发送邮件（测试用）")
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("groupbrief.daily_auto")
    os.chdir(PROJECT_ROOT)  # 保证 .env 等相对路径正确（计划任务的工作目录不固定）

    run_date = args.date or datetime.now().date().isoformat()
    log.info("===== 每日自动任务开始 run_date=%s =====", run_date)

    result = run_daily_v2_job(
        run_date,
        settings=get_settings(),
        skip_email=args.skip_email,
    )
    log.info("===== 每日自动任务结束：%s =====", result)
    return 0 if result.get("status") in {
        "success", "partial", "skipped", "already_completed", "already_running"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
