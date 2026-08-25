"""唯一的 V2 每日生成与邮件调度任务。

状态写入 output/.scheduler/<run_date>.json。生成和邮件分别记录开始与完成
时间；生成阶段被进程中断后可以在全局生成锁保护下续跑未完成群，邮件阶段的
结果未知保护保持不变，避免重复发送外部消息。
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
from app.services.email_service import email_delivery_config_error
from app.v2.constants import SCHEDULER_STATE_CORRUPT
from app.scheduler.outcome import ProcessExitCode, attach_outcome, summarize_results

logger = get_logger("groupbrief.scheduler")
_STATE_LOCK = threading.RLock()
# 兼容旧测试/调用名，底层已改为 V1/V2 共用锁。
_daily_mutex = generation_mutex


class ScheduleStateCorruptionError(RuntimeError):
    """已有 scheduler 状态损坏；禁止用新任务状态覆盖。"""


class DailyScheduleState:
    def __init__(self, output_root: Path | str):
        self.root = Path(output_root) / ".scheduler"

    def path(self, run_date: str) -> Path:
        if parse_date(run_date) is None:
            raise ValueError("run_date 必须是有效的 YYYY-MM-DD 日期")
        return self.root / f"{run_date}.json"

    def load(self, run_date: str) -> dict:
        path = self.path(run_date)
        if not path.exists():
            return {"run_date": run_date}
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return self._corrupt_state(run_date, path, "read_failed")
        except json.JSONDecodeError:
            return self._corrupt_state(run_date, path, "json_invalid")
        schema_error = self._schema_error(parsed, run_date)
        if schema_error:
            return self._corrupt_state(run_date, path, schema_error)
        return parsed

    def _corrupt_state(self, run_date: str, path: Path, reason: str) -> dict:
        return {
            "run_date": run_date,
            "state_status": "corrupt",
            "error_type": SCHEDULER_STATE_CORRUPT,
            "state_error_reason": reason,
            "state_file": path.name,
            "generation_hold": True,
            "email_hold": True,
            "needs_manual_review": True,
            "detail": "调度状态文件损坏，已阻止自动补偿、生成和邮件发送",
        }

    @staticmethod
    def _schema_error(data: object, run_date: str) -> str | None:
        if not isinstance(data, dict):
            return "root_not_object"
        if data.get("run_date") != run_date:
            return "run_date_invalid"
        timestamp_fields = (
            "generation_started_at",
            "generation_completed_at",
            "generation_resumed_at",
            "generation_recovered_at",
            "email_started_at",
            "email_completed_at",
            "last_invocation_completed_at",
            "updated_at",
        )
        for field in timestamp_fields:
            value = data.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                return f"{field}_invalid"
            try:
                datetime.fromisoformat(value.strip())
            except ValueError:
                return f"{field}_invalid"
        for field in ("generation_status", "email_status"):
            value = data.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return f"{field}_invalid"
        for field in ("generation_hold", "email_hold"):
            value = data.get(field)
            if value is not None and not isinstance(value, bool):
                return f"{field}_invalid"
        exit_code = data.get("last_invocation_exit_code")
        if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
            return "last_invocation_exit_code_invalid"
        invocation_status = data.get("last_invocation_status")
        if invocation_status is not None and (
            not isinstance(invocation_status, str) or not invocation_status.strip()
        ):
            return "last_invocation_status_invalid"
        generation_results = data.get("generation_results")
        if generation_results is not None and (
            not isinstance(generation_results, list)
            or any(not isinstance(item, dict) for item in generation_results)
        ):
            return "generation_results_invalid"
        if data.get("generation_started_at") and not data.get("generation_status"):
            return "generation_status_missing"
        if data.get("generation_completed_at") and not data.get("generation_status"):
            return "generation_status_missing"
        if data.get("email_started_at") and not data.get("email_status"):
            return "email_status_missing"
        if data.get("email_completed_at") and not data.get("email_status"):
            return "email_status_missing"
        if not any(
            data.get(field)
            for field in (
                "generation_started_at",
                "generation_completed_at",
                "email_started_at",
                "email_completed_at",
            )
        ):
            return "lifecycle_marker_missing"
        return None

    def update(self, run_date: str, **fields) -> dict:
        with _STATE_LOCK:
            data = self.load(run_date)
            if data.get("state_status") == "corrupt":
                raise ScheduleStateCorruptionError("调度状态文件损坏，禁止自动覆盖")
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
        return attach_outcome(
            {"status": "failed", "error_type": "INVALID_RUN_DATE", "detail": "run_date 格式无效"}
        )

    try:
        with _daily_mutex():
            result = _run_locked(settings, parsed_date, skip_email=skip_email)
    except GenerationBusyError as exc:
        logger.info("V2 每日任务未领取：%s", exc)
        result = {"status": "already_running", "detail": str(exc)}
    except Exception as exc:
        logger.exception("V2 每日任务异常")
        result = {"status": "failed", "detail": str(exc)[:300]}
    return _finalize_invocation(settings, parsed_date.isoformat(), result)


def _finalize_invocation(settings: Settings, run_date: str, result: dict) -> dict:
    finalized = attach_outcome(result)
    logger.info(
        "V2 每日任务终态：run_date=%s source_status=%s outcome=%s exit_code=%d",
        run_date,
        finalized.get("status"),
        finalized["outcome_status"],
        finalized["exit_code"],
    )
    if finalized["outcome_status"] == "already_running":
        return finalized

    state_store = DailyScheduleState(settings.output_dir)
    path = state_store.path(run_date)
    if not path.is_file():
        return finalized
    state = state_store.load(run_date)
    if state.get("state_status") == "corrupt":
        return finalized
    state_store.update(
        run_date,
        last_invocation_source_status=str(finalized.get("status") or ""),
        last_invocation_status=finalized["outcome_status"],
        last_invocation_exit_code=finalized["exit_code"],
        last_invocation_completed_at=_now_iso(),
    )
    return finalized


def _run_locked(settings: Settings, run_date: date, *, skip_email: bool) -> dict:
    run_date_text = run_date.isoformat()
    state_store = DailyScheduleState(settings.output_dir)
    state = state_store.load(run_date_text)
    if state.get("state_status") == "corrupt":
        logger.error("V2 每日任务已阻断：run_date=%s scheduler state corrupt", run_date_text)
        return {
            "status": "blocked",
            "run_date": run_date_text,
            "error_type": SCHEDULER_STATE_CORRUPT,
            "detail": "调度状态文件损坏，需人工复核",
        }
    repo.init_db(settings)
    repo.apply_db_settings(settings)

    generation_results = state.get("generation_results") or []
    if not state.get("generation_completed_at"):
        if state.get("generation_started_at"):
            try:
                resume_count = int(state.get("generation_resume_count") or 0) + 1
            except (TypeError, ValueError):
                resume_count = 1
            state = state_store.update(
                run_date_text,
                generation_status="resuming",
                generation_hold=False,
                generation_error="",
                generation_resumed_at=_now_iso(),
                generation_resume_count=resume_count,
            )
            logger.warning(
                "V2 每日生成检测到中断，开始安全续跑：run_date=%s resume_count=%d",
                run_date_text,
                resume_count,
            )
        else:
            state = state_store.update(
                run_date_text,
                generation_started_at=_now_iso(),
                generation_status="running",
                generation_hold=False,
                generation_error="",
            )
        try:
            generation_results = DailyPipeline(settings=settings).generate_all(
                run_date=run_date_text, acquire_lock=False
            )
        except Exception as exc:
            state_store.update(
                run_date_text,
                generation_status="interrupted",
                generation_hold=True,
                generation_error=str(exc)[:300],
            )
            raise
        generation_status = _generation_status(generation_results)
        state = state_store.update(
            run_date_text,
            generation_completed_at=_now_iso(),
            generation_status=generation_status,
            generation_results=_compact_results(generation_results),
            generation_hold=False,
            generation_error="",
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

    generation_status = str(state.get("generation_status") or "failed")
    if generation_status in {"failed", "blocked", "not_run"}:
        state_store.update(
            run_date_text,
            email_status="skipped_generation_not_successful",
            email_completed_at=_now_iso(),
            email_detail=f"生成终态为 {generation_status}，未调用邮件",
        )
        return {
            "status": generation_status,
            "run_date": run_date_text,
            "generation_status": generation_status,
            "email_status": "skipped_generation_not_successful",
        }
    if generation_status == "partial" and not settings.email_send_partial_report:
        state_store.update(
            run_date_text,
            email_status="skipped_partial_disabled",
            email_completed_at=_now_iso(),
            email_detail="生成部分成功且未启用部分报告邮件",
        )
        return {
            "status": "partial",
            "run_date": run_date_text,
            "generation_status": "partial",
            "email_status": "skipped_partial_disabled",
        }

    if state.get("email_completed_at"):
        if state.get("email_status") == "unknown":
            return {
                "status": "blocked",
                "run_date": run_date_text,
                "generation_status": generation_status,
                "email_status": "unknown",
                "error_type": "EMAIL_RESULT_UNKNOWN",
                "detail": "邮件发送结果未知，需人工检查",
            }
        if state.get("email_status") in {"partial", "failed", "failed_before_submit"}:
            return {
                "status": "partial",
                "run_date": run_date_text,
                "generation_status": generation_status,
                "email_status": state.get("email_status"),
            }
        completed_status = generation_status if generation_status != "success" else "already_completed"
        return {
            "status": completed_status,
            "run_date": run_date_text,
            "generation_status": generation_status,
            "email_status": state.get("email_status"),
        }
    if not settings.email_enabled:
        state_store.update(
            run_date_text,
            email_status="skipped_disabled",
            email_completed_at=_now_iso(),
            email_detail="邮件未启用或 SMTP 未配置",
        )
        return {
            "status": generation_status,
            "run_date": run_date_text,
            "email_status": "skipped_disabled",
        }
    email_config_error = email_delivery_config_error(settings)
    if email_config_error:
        state_store.update(
            run_date_text,
            email_status="failed_config",
            email_completed_at=_now_iso(),
            email_error=email_config_error,
            email_detail="邮件配置无效，未启动发送子进程",
        )
        return {
            "status": "partial",
            "run_date": run_date_text,
            "generation_status": generation_status,
            "email_status": "failed_config",
            "error_type": "EMAIL_PROVIDER_CONFIG_INVALID",
            "detail": email_config_error,
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
    if proc.returncode == int(ProcessExitCode.SUCCESS):
        email_status = "sent"
        result_status = generation_status
        email_hold = False
    elif proc.returncode == int(ProcessExitCode.PARTIAL):
        email_status = "partial"
        result_status = "partial"
        email_hold = False
    elif proc.returncode == int(ProcessExitCode.BLOCKED):
        email_status = "unknown"
        result_status = "blocked"
        email_hold = True
    else:
        email_status = "failed_before_submit"
        result_status = "partial"
        email_hold = False
    state_store.update(
        run_date_text,
        email_status=email_status,
        email_completed_at=_now_iso(),
        email_exit_code=proc.returncode,
        email_detail=output_tail,
        email_hold=email_hold,
        email_error=(
            "逐群邮件账本存在结果未知项，禁止自动重复发送"
            if email_status == "unknown"
            else ""
        ),
    )
    result = {
        "status": result_status,
        "run_date": run_date_text,
        "generation_status": generation_status,
        "email_status": email_status,
    }
    if email_status == "unknown":
        result.update(
            error_type="EMAIL_RESULT_UNKNOWN",
            detail="逐群邮件账本存在结果未知项，需人工核对",
        )
    return result


def _generation_status(results: list[dict]) -> str:
    return str(summarize_results(results)["outcome_status"])


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
