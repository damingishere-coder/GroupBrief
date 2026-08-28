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
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config.settings import PROJECT_ROOT, Settings, get_settings
from app.core.logging import get_logger
from app.core.observability import log_event
from app.db import repository as repo
from app.pipeline.daily_pipeline import DailyPipeline, parse_date
from app.services.generation_runtime import GenerationBusyError, generation_mutex
from app.services.email_service import email_delivery_config_error
from app.v2.constants import IMAGE_GENERATION_FAILED, SCHEDULER_STATE_CORRUPT
from app.v2.run_store import _run_mutex
from app.scheduler.outcome import ProcessExitCode, attach_outcome, summarize_results
from app.scheduler.task_manifest import (
    build_expected_groups,
    expected_group_ids,
    manifest_fields,
)

logger = get_logger("groupbrief.scheduler")
# 兼容旧测试/调用名，底层已改为 V1/V2 共用锁。
_daily_mutex = generation_mutex


class ScheduleStateCorruptionError(RuntimeError):
    """已有 scheduler 状态损坏；禁止用新任务状态覆盖。"""


class ScheduleStateVersionConflictError(RuntimeError):
    """scheduler 状态版本与调用方确认的版本不一致。"""


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
            "generation_invocation_completed_at",
            "generation_resumed_at",
            "generation_recovered_at",
            "email_started_at",
            "email_completed_at",
            "last_invocation_completed_at",
            "updated_at",
            "owner_busy_at",
            "next_retry_at",
            "manifest_created_at",
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
        expected_groups = data.get("expected_groups")
        if expected_groups is not None:
            if not isinstance(expected_groups, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("group_id"), int)
                or item.get("group_id", 0) <= 0
                for item in expected_groups
            ):
                return "expected_groups_invalid"
            if data.get("manifest_version") != 1:
                return "manifest_version_invalid"
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
                "owner_busy_at",
                "manifest_created_at",
            )
        ):
            return "lifecycle_marker_missing"
        return None

    def update(self, run_date: str, **fields) -> dict:
        path = self.path(run_date)
        with _run_mutex(path):
            data = self.load(run_date)
            if data.get("state_status") == "corrupt":
                raise ScheduleStateCorruptionError("调度状态文件损坏，禁止自动覆盖")
            data.update(fields)
            data["run_date"] = run_date
            data.setdefault("run_id", f"groupbrief:{run_date}:{uuid.uuid4().hex[:12]}")
            data["state_version"] = int(data.get("state_version") or 0) + 1
            data["updated_at"] = _now_iso()
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
            return data

    def compare_and_update(
        self,
        run_date: str,
        *,
        expected_state_version: int,
        **fields,
    ) -> dict:
        """在同一个文件锁内校验版本并原子更新，供显式恢复操作使用。"""

        path = self.path(run_date)
        with _run_mutex(path):
            data = self.load(run_date)
            if data.get("state_status") == "corrupt":
                raise ScheduleStateCorruptionError("调度状态文件损坏，禁止自动覆盖")
            current_version = int(data.get("state_version") or 0)
            if current_version != expected_state_version:
                raise ScheduleStateVersionConflictError(
                    f"调度状态已变化：expected={expected_state_version} actual={current_version}"
                )
            data.update(fields)
            data["run_date"] = run_date
            data.setdefault("run_id", f"groupbrief:{run_date}:{uuid.uuid4().hex[:12]}")
            data["state_version"] = current_version + 1
            data["updated_at"] = _now_iso()
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
        result = {
            "status": "already_running",
            "error_type": "GENERATION_OWNER_BUSY",
            "retryable": True,
            "detail": str(exc),
        }
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
    state_snapshot = DailyScheduleState(settings.output_dir).load(run_date)
    log_event(
        logger,
        "DAILY_INVOCATION_FINISHED",
        run_id=state_snapshot.get("run_id"),
        run_date=run_date,
        stage="DAILY",
        status=finalized.get("outcome_status"),
        response_code=finalized.get("exit_code"),
        error_type=finalized.get("error_type", ""),
        error_summary=finalized.get("detail", ""),
    )
    state_store = DailyScheduleState(settings.output_dir)
    path = state_store.path(run_date)
    if not path.is_file() and finalized["outcome_status"] != "already_running":
        return finalized
    state = state_store.load(run_date)
    if state.get("state_status") == "corrupt":
        return finalized
    fields = {
        "last_invocation_source_status": str(finalized.get("status") or ""),
        "last_invocation_status": finalized["outcome_status"],
        "last_invocation_exit_code": finalized["exit_code"],
        "last_invocation_completed_at": _now_iso(),
    }
    if finalized["outcome_status"] == "already_running":
        busy_count = int(state.get("owner_busy_count") or 0) + 1
        now = datetime.now().astimezone()
        fields.update(
            owner_busy_at=now.isoformat(),
            owner_busy_count=busy_count,
            next_retry_at=(now.replace(microsecond=0) + timedelta(minutes=5)).isoformat(),
        )
    state_store.update(run_date, **fields)
    return finalized


def ensure_daily_manifest(
    settings: Settings,
    run_date: str,
    *,
    pipeline: DailyPipeline | None = None,
    state_store: DailyScheduleState | None = None,
    state: dict | None = None,
) -> dict:
    """为 48 小时活动窗口惰性补齐任务清单，不执行生成或发送。"""
    parsed = parse_date(run_date)
    if parsed is None:
        raise ValueError("run_date 必须是有效的 YYYY-MM-DD 日期")
    state_store = state_store or DailyScheduleState(settings.output_dir)
    state = state or state_store.load(run_date)
    if state.get("state_status") == "corrupt":
        return state
    if isinstance(state.get("expected_groups"), list):
        return state
    if pipeline is None:
        repo.init_db(settings)
        repo.apply_db_settings(settings)
        pipeline = DailyPipeline(settings=settings)
    loader = getattr(pipeline, "_load_groups", None)
    resolver = getattr(pipeline, "period_resolver", None)
    if not callable(loader) or resolver is None:
        return state
    expected = build_expected_groups(
        loader(),
        parsed,
        timezone=settings.app_timezone,
        resolver=resolver,
    )
    manifest = manifest_fields(expected)
    if state.get("generation_completed_at"):
        # 部署新清单合同前已经完成的活动窗口只补状态投影。
        manifest["manifest_source"] = "legacy_current_config_compat"
    return state_store.update(run_date, **manifest)


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
    pipeline = DailyPipeline(settings=settings)
    state = ensure_daily_manifest(
        settings,
        run_date_text,
        pipeline=pipeline,
        state_store=state_store,
        state=state,
    )
    manifest_ids = (
        expected_group_ids(state)
        if isinstance(state.get("expected_groups"), list)
        else None
    )

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
            if manifest_ids is None:
                # 兼容显式测试替身与上一版注入点；生产 DailyPipeline 必有任务清单。
                generation_results = pipeline.generate_all(
                    run_date=run_date_text,
                    acquire_lock=False,
                )
            elif manifest_ids:
                generation_results = pipeline.generate_all(
                    run_date=run_date_text,
                    group_ids=manifest_ids,
                    group_overrides={
                        int(row["group_id"]): row
                        for row in state.get("expected_groups", [])
                        if isinstance(row, dict)
                        and isinstance(row.get("group_id"), int)
                    },
                    acquire_lock=False,
                )
            else:
                generation_results = [
                    {
                        "status": "no_groups",
                        "reason": "当日没有符合群级统计规则的任务",
                    }
                ]
        except Exception as exc:
            state_store.update(
                run_date_text,
                generation_status="interrupted",
                generation_hold=True,
                generation_error=str(exc)[:300],
            )
            writer = getattr(pipeline, "_write_runtime_status_safe", None)
            if callable(writer):
                writer([run_date_text])
            raise
        generation_status = _generation_status(generation_results)
        completion_fields = {
            "generation_invocation_completed_at": _now_iso(),
            "generation_status": generation_status,
            "generation_results": _compact_results(generation_results),
            "generation_hold": generation_status in {"blocked", "failed", "partial"},
            "generation_error": "",
        }
        if _generation_results_terminal(generation_results):
            completion_fields["generation_completed_at"] = _now_iso()
        state = state_store.update(run_date_text, **completion_fields)
        writer = getattr(pipeline, "_write_runtime_status_safe", None)
        if callable(writer):
            writer([run_date_text])
    else:
        state = state_store.load(run_date_text)
        try:
            state = _reconcile_completed_generation(
                settings,
                run_date_text,
                state_store,
                state,
            )
        except Exception:
            logger.exception(
                "已完成生成批次的可信图片对账失败，保留原终态：run_date=%s",
                run_date_text,
            )
        generation_results = state.get("generation_results") or generation_results

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

    if (
        state.get("email_recovery_required")
        and state.get("email_completed_at")
        and state.get("email_status") != "unknown"
    ):
        email_history = list(state.get("email_history") or [])
        email_history.append(
            {
                "status": str(state.get("email_status") or ""),
                "completed_at": str(state.get("email_completed_at") or ""),
                "detail": str(state.get("email_detail") or "")[-800:],
            }
        )
        state = state_store.update(
            run_date_text,
            email_history=email_history[-10:],
            email_status="recovery_pending",
            email_started_at=None,
            email_completed_at=None,
            email_hold=False,
            email_error="",
            email_detail="可信生图恢复完成；逐群邮件账本将只提交尚未确认发送的群",
        )

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
    if state.get("email_recovery_required"):
        for group_name in state.get("generation_recovery_groups") or []:
            if isinstance(group_name, str) and group_name.strip():
                command.extend(["--group", group_name.strip()])
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
        email_recovery_required=False,
        email_recovered_at=(
            _now_iso() if state.get("email_recovery_required") else state.get("email_recovered_at")
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


def _generation_results_terminal(results: list[dict]) -> bool:
    """批次内所有群都已成功或进入明确人工/最终终态时才封存批次。"""
    if not results:
        return False
    terminal_statuses = {
        "success",
        "ready_to_send",
        "already_completed",
        "skipped",
        "no_groups",
        "held",
        "blocked",
        "failed_final",
    }
    return all(str(item.get("status") or "").lower() in terminal_statuses for item in results)


def _reconcile_completed_generation(
    settings: Settings,
    run_date: str,
    state_store: DailyScheduleState,
    state: dict,
) -> dict:
    """只对带可信 Codex thread_id 候选的失败群做无新调用收口。"""
    if state.get("generation_status") != "partial":
        return state
    original_results = state.get("generation_results")
    if not isinstance(original_results, list):
        return state

    pipeline = DailyPipeline(settings=settings)
    generator = pipeline.image_generator
    can_reconcile = getattr(generator, "can_reconcile_without_generation", None)
    if not callable(can_reconcile):
        return state

    groups = pipeline._load_groups()
    groups_by_name = {
        (group.display_name or group.wechat_group_name): group for group in groups
    }
    recovery_ids: list[int] = []
    for result in original_results:
        if not isinstance(result, dict):
            continue
        if result.get("error_type") != IMAGE_GENERATION_FAILED:
            continue
        group_name = str(result.get("group_name") or "")
        group = groups_by_name.get(group_name)
        if group is None or group.id is None:
            continue
        run = pipeline.store.load_run(group_name, run_date)
        image_job = run.get("image_job") if isinstance(run.get("image_job"), dict) else {}
        job_id = str(image_job.get("job_id") or "")
        prompt_path = pipeline.store.prompt_path(group_name, run_date)
        if prompt_path.is_file() and can_reconcile(
            prompt_path,
            job_id,
        ):
            recovery_ids.append(int(group.id))

    if not recovery_ids:
        return state

    recovery_results = pipeline.generate_all(
        run_date=run_date,
        group_ids=recovery_ids,
        force=False,
        acquire_lock=False,
    )
    replacements = {
        str(item.get("group_name") or ""): item
        for item in recovery_results
        if isinstance(item, dict) and item.get("group_name")
    }
    merged_results = [
        replacements.get(str(item.get("group_name") or ""), item)
        if isinstance(item, dict)
        else item
        for item in original_results
    ]
    recovered_groups = sorted(
        name
        for name, item in replacements.items()
        if str(item.get("status") or "") in {"ready_to_send", "success", "skipped"}
    )
    history = list(state.get("generation_history") or [])
    history.append(
        {
            "status": str(state.get("generation_status") or ""),
            "completed_at": str(state.get("generation_completed_at") or ""),
            "results": original_results,
        }
    )
    next_status = _generation_status(merged_results)
    logger.info(
        "V2 已完成批次可信图片对账：run_date=%s groups=%s status=%s results=%s",
        run_date,
        recovered_groups,
        next_status,
        _compact_results(recovery_results),
    )
    return state_store.update(
        run_date,
        generation_original_status=(
            state.get("generation_original_status") or state.get("generation_status")
        ),
        generation_history=history[-10:],
        generation_status=next_status,
        generation_results=_compact_results(merged_results),
        generation_recovered_at=_now_iso() if recovered_groups else state.get("generation_recovered_at"),
        generation_recovery_groups=recovered_groups,
        generation_recovery_results=_compact_results(recovery_results),
        email_recovery_required=bool(recovered_groups),
    )


def _compact_results(results: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for item in results:
        compact.append(
            {
                key: item.get(key)
                for key in (
                    "group_name",
                    "status",
                    "error_type",
                    "detail",
                    "reason",
                    "failed_stage",
                    "receipt_source",
                    "recovery_status",
                    "recovered_at",
                    "codex_thread_id",
                )
                if item.get(key) not in (None, "")
            }
        )
    return compact


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
