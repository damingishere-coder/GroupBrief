"""唯一的 V2 每日生成与邮件调度任务。

状态写入 output/.scheduler/<run_date>.json。生成和邮件分别记录开始与完成
时间；若进程在阶段开始后、完成标记前退出，下一次启动只报告结果未知，不自动
重试可能产生计费或重复邮件的副作用。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config.settings import PROJECT_ROOT, Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline, parse_date
from app.services.generation_runtime import GenerationBusyError, generation_mutex

logger = get_logger("groupbrief.scheduler")
_STATE_LOCK = threading.RLock()
# 兼容旧测试/调用名，底层已改为 V1/V2 共用锁。
_daily_mutex = generation_mutex


class DailyScheduleState:
    def __init__(self, output_root: Path | str):
        self.root = Path(output_root) / ".scheduler"

    def path(self, run_date: str) -> Path:
        if parse_date(run_date) is None:
            raise ValueError("run_date 必须是有效的 YYYY-MM-DD 日期")
        return self.root / f"{run_date}.json"

    def load(self, run_date: str) -> dict:
        path = self.path(run_date)
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except (OSError, json.JSONDecodeError):
            pass
        return {"run_date": run_date}

    def update(self, run_date: str, **fields) -> dict:
        with _STATE_LOCK:
            data = self.load(run_date)
            data.update(fields)
            data["run_date"] = run_date
            data["updated_at"] = _now_iso()
            path = self.path(run_date)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
            return data


def run_daily_v2_job(
    run_date: str | None = None,
    *,
    settings: Settings | None = None,
    skip_email: bool = False,
) -> dict:
    settings = settings or get_settings()
    tz = ZoneInfo(settings.app_timezone)
    run_date = run_date or datetime.now(tz).date().isoformat()
    parsed_date = parse_date(run_date)
    if parsed_date is None:
        return {"status": "failed", "error_type": "INVALID_RUN_DATE", "detail": "run_date 格式无效"}

    try:
        with _daily_mutex():
            return _run_locked(settings, parsed_date, skip_email=skip_email)
    except GenerationBusyError as exc:
        logger.info("V2 每日任务未领取：%s", exc)
        return {"status": "already_running", "detail": str(exc)}
    except Exception as exc:
        logger.exception("V2 每日任务异常")
        return {"status": "failed", "detail": str(exc)[:300]}


def _run_locked(settings: Settings, run_date: date, *, skip_email: bool) -> dict:
    run_date_text = run_date.isoformat()
    state_store = DailyScheduleState(settings.output_dir)
    state = state_store.load(run_date_text)
    repo.init_db(settings)
    repo.apply_db_settings(settings)

    generation_results = state.get("generation_results") or []
    if not state.get("generation_completed_at"):
        if state.get("generation_started_at"):
            state_store.update(
                run_date_text,
                generation_status="unknown",
                generation_hold=True,
                generation_error="生成阶段曾启动但没有完成标记，禁止自动重复生成",
            )
            return {
                "status": "blocked",
                "error_type": "GENERATION_RESULT_UNKNOWN",
                "detail": "生成阶段结果未知，需人工检查",
            }
        state_store.update(
            run_date_text,
            generation_started_at=_now_iso(),
            generation_status="running",
            generation_hold=False,
            generation_error="",
        )
        generation_results = DailyPipeline(settings=settings).generate_all(
            run_date=run_date_text, acquire_lock=False
        )
        generation_status = _generation_status(generation_results)
        state = state_store.update(
            run_date_text,
            generation_completed_at=_now_iso(),
            generation_status=generation_status,
            generation_results=_compact_results(generation_results),
        )
    else:
        state = state_store.load(run_date_text)

    if skip_email:
        return {
            "status": state.get("generation_status", "completed"),
            "run_date": run_date_text,
            "generation_results": generation_results,
            "email_status": "skipped_by_request",
        }

    if state.get("email_completed_at"):
        return {
            "status": "already_completed",
            "run_date": run_date_text,
            "generation_status": state.get("generation_status"),
            "email_status": state.get("email_status"),
        }
    if not settings.email_enabled or not settings.email_smtp_host:
        state_store.update(
            run_date_text,
            email_status="skipped_disabled",
            email_completed_at=_now_iso(),
            email_detail="邮件未启用或 SMTP 未配置",
        )
        return {
            "status": state.get("generation_status", "completed"),
            "run_date": run_date_text,
            "email_status": "skipped_disabled",
        }
    if state.get("email_started_at"):
        state_store.update(
            run_date_text,
            email_status="unknown",
            email_hold=True,
            email_error="邮件阶段曾启动但没有完成标记，禁止自动重复发送",
        )
        return {
            "status": "blocked",
            "error_type": "EMAIL_RESULT_UNKNOWN",
            "detail": "邮件发送结果未知，需人工检查",
        }

    state_store.update(
        run_date_text,
        email_started_at=_now_iso(),
        email_status="running",
        email_hold=False,
        email_error="",
    )
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "send_daily_email.py"),
        "--run-date",
        run_date_text,
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        state_store.update(
            run_date_text,
            email_status="unknown",
            email_hold=True,
            email_error="邮件子进程超时，结果未知，禁止自动重试",
        )
        return {
            "status": "blocked",
            "error_type": "EMAIL_RESULT_UNKNOWN",
            "detail": "邮件子进程超时，结果未知",
        }
    except Exception as exc:
        state_store.update(
            run_date_text,
            email_status="failed_before_submit",
            email_completed_at=_now_iso(),
            email_error=str(exc)[:300],
        )
        return {"status": "failed", "detail": str(exc)[:300]}

    output_tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-800:]
    email_status = "sent" if proc.returncode == 0 else "failed"
    state_store.update(
        run_date_text,
        email_status=email_status,
        email_completed_at=_now_iso(),
        email_exit_code=proc.returncode,
        email_detail=output_tail,
    )
    return {
        "status": "success" if proc.returncode == 0 else "partial",
        "run_date": run_date_text,
        "generation_status": state.get("generation_status"),
        "email_status": email_status,
    }


def _generation_status(results: list[dict]) -> str:
    statuses = {str(item.get("status") or "") for item in results}
    if statuses and statuses <= {"ready_to_send", "skipped", "no_groups"}:
        return "success"
    if "failed" in statuses and len(statuses) == 1:
        return "failed"
    if "failed" in statuses:
        return "partial"
    return "success"


def _compact_results(results: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for item in results:
        compact.append(
            {
                key: item.get(key)
                for key in ("group_name", "status", "error_type", "detail", "reason")
                if item.get(key) not in (None, "")
            }
        )
    return compact


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
