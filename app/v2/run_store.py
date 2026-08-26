"""V2 运行状态存储：output/<群名称>/<日期>/run.json。

run.json 是每个群每次运行的唯一状态文件（路线文档 §十）。
状态机：PENDING → DATA_READY → RANKING_READY → PROMPT_READY →
IMAGE_READY → READY_TO_SEND → SENT / FAILED。
已有状态文件无法可信解析时进入合成的 CORRUPT 只读隔离态，不参与自动推进。

同时统一管理该群该日期的输出文件命名与目录。
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from app.core.path_security import resolve_within, validate_iso_date, validate_path_label
from app.services.handoff_service import safe_dir_name
from app.v2.constants import (
    CORRUPT,
    FILE_IMAGE,
    FILE_IMAGE_PREVIOUS,
    FILE_IMAGE_REGENERATING,
    FILE_MESSAGES,
    FILE_PROMPT,
    FILE_PROMPT_ORIGINAL,
    FILE_RANKING_JSON,
    FILE_RANKING_TXT,
    FILE_RUN,
    IMAGE_READY,
    PENDING,
    PROMPT_READY,
    READY_TO_SEND,
    RUN_STATE_CORRUPT,
    SENT,
    STATUS_FLOW,
)


_RUN_WRITE_LOCK = threading.RLock()
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_PERSISTED_STATUSES = frozenset(STATUS_FLOW) - {CORRUPT}


class RunStateCorruptionError(RuntimeError):
    """已有 run.json 损坏；任何自动写入都必须 fail closed。"""


def validate_run_date(value: str) -> str:
    """校验 V2 运行目录日期，拒绝路径段和不存在的日历日期。"""
    return validate_iso_date(value, field_name="run_date")


@contextmanager
def _run_mutex(path: Path, timeout_seconds: float = 10.0) -> Iterator[None]:
    """同一 run.json 的跨线程、跨进程互斥。

    Windows 生产环境使用命名互斥锁；其他平台保留进程内锁，便于测试。
    """
    acquired = _RUN_WRITE_LOCK.acquire(timeout=max(timeout_seconds, 0.1))
    if not acquired:
        raise TimeoutError(f"等待运行状态锁超时：{path}")
    handle = None
    owns_handle = False
    try:
        if os.name == "nt":
            import ctypes

            digest = hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:32]
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\GroupBrief.Run.{digest}")
            if not handle:
                raise OSError(f"无法创建运行状态互斥锁：{path}")
            wait_code = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
            if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise TimeoutError(f"等待运行状态互斥锁超时：{path}")
            owns_handle = True
        yield
    finally:
        if handle:
            import ctypes

            if owns_handle:
                ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        _RUN_WRITE_LOCK.release()


def _parse_timestamp(value: object, reference: datetime) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if reference.tzinfo is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo)
    elif reference.tzinfo is None and parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _has_unresolved_send_attempt(data: dict) -> bool:
    for prefix in ("text", "image"):
        started = data.get(f"{prefix}_attempt_started_at")
        finished = data.get(f"{prefix}_attempt_finished_at")
        verified = data.get(f"{prefix}_verified_at") or data.get(f"{prefix}_sent_at")
        if started and not finished and not verified:
            return True
    return False


class RunStore:
    def __init__(self, output_root: Path | str):
        self.root = Path(output_root)

    # ---------- 路径 ----------

    def _group_root(self, group_name: str) -> Path:
        validate_path_label(group_name, field_name="group_name")
        return resolve_within(self.root, safe_dir_name(group_name))

    def group_dir(self, group_name: str, run_date: str) -> Path:
        valid_date = validate_run_date(run_date)
        return resolve_within(self._group_root(group_name), valid_date)

    def run_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RUN

    # 输出文件绝对路径
    def messages_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_MESSAGES

    def ranking_json_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RANKING_JSON

    def ranking_txt_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RANKING_TXT

    def prompt_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_PROMPT

    def original_prompt_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_PROMPT_ORIGINAL

    def image_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_IMAGE

    def previous_image_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_IMAGE_PREVIOUS

    def regenerating_image_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_IMAGE_REGENERATING

    # ---------- run.json ----------

    def _corrupt_run(self, group_name: str, run_date: str, path: Path, reason: str) -> dict:
        try:
            state_file = str(path.relative_to(self.root))
        except ValueError:
            state_file = path.name
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
        except OSError:
            updated_at = ""
        return {
            "group_name": group_name,
            "run_date": run_date,
            "status": CORRUPT,
            "state_status": "corrupt",
            "error_type": RUN_STATE_CORRUPT,
            "state_error_reason": reason,
            "state_file": state_file,
            "updated_at": updated_at,
            "send_hold": True,
            "needs_manual_review": True,
            "detail": "运行状态文件损坏，已阻止自动覆盖、生成和发送",
        }

    @staticmethod
    def _run_schema_error(data: object, run_date: str) -> str | None:
        if not isinstance(data, dict):
            return "root_not_object"
        group_name = data.get("group_name")
        if not isinstance(group_name, str) or not group_name.strip():
            return "group_name_invalid"
        stored_date = data.get("run_date")
        if not isinstance(stored_date, str) or stored_date != run_date:
            return "run_date_invalid"
        status = data.get("status")
        if not isinstance(status, str) or status not in _PERSISTED_STATUSES:
            return "status_invalid"
        return None

    def _read_run_file(self, path: Path, group_name: str, run_date: str) -> dict:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return self._corrupt_run(group_name, run_date, path, "read_failed")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._corrupt_run(group_name, run_date, path, "json_invalid")
        schema_error = self._run_schema_error(data, run_date)
        if schema_error:
            return self._corrupt_run(group_name, run_date, path, schema_error)
        return data

    @staticmethod
    def _is_corrupt(data: dict) -> bool:
        return data.get("status") == CORRUPT and data.get("error_type") == RUN_STATE_CORRUPT

    def load_run(self, group_name: str, run_date: str) -> dict:
        path = self.run_path(group_name, run_date)
        if path.exists():
            return self._read_run_file(path, group_name, run_date)
        return {"group_name": group_name, "run_date": run_date, "status": PENDING}

    def save_run(self, group_name: str, run_date: str, data: dict) -> dict:
        with _RUN_WRITE_LOCK:
            path = self.run_path(group_name, run_date)
            if path.exists():
                existing = self._read_run_file(path, group_name, run_date)
                if self._is_corrupt(existing):
                    raise RunStateCorruptionError("运行状态文件损坏，禁止自动覆盖")
            path.parent.mkdir(parents=True, exist_ok=True)
            data = dict(data)
            data.setdefault("group_name", group_name)
            data.setdefault("status", PENDING)
            data["run_date"] = run_date
            schema_error = self._run_schema_error(data, run_date)
            if schema_error:
                raise ValueError(f"run.json 写入数据不符合 Schema：{schema_error}")
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            return data

    def update(self, group_name: str, run_date: str, **fields) -> dict:
        """加载 → 合并字段 → 保存，返回最新 run。"""
        with _RUN_WRITE_LOCK:
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                raise RunStateCorruptionError("运行状态文件损坏，禁止自动覆盖")
            data.update(fields)
            return self.save_run(group_name, run_date, data)

    # ---------- Prompt 外部调用 claim / result ----------

    def claim_prompt_operation(
        self,
        group_name: str,
        run_date: str,
        *,
        input_hash: str,
        force: bool = False,
    ) -> tuple[str | None, dict, str]:
        """原子领取一次 Prompt 生成操作。

        已开始但没有完成/结果记录的调用一律转为 unknown；普通恢复不得再次
        调用外部模型。结果已先写入 run.json 时可由调用方无费用地继续提交文件。
        """
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                return None, data, "state_corrupt"

            operation_status = str(data.get("prompt_operation_status") or "")
            same_input = data.get("prompt_operation_input_hash") == input_hash
            if operation_status == "result_recorded" and same_input:
                result = data.get("prompt_operation_result")
                if isinstance(result, dict) and isinstance(result.get("prompt"), str):
                    return None, data, "result_recorded"
            if operation_status == "result_recorded" and not same_input:
                data.update(
                    prompt_operation_status="unknown",
                    prompt_hold=True,
                    prompt_hold_reason="PROMPT_INPUT_CHANGED_AFTER_RESULT",
                    prompt_operation_error="AI 结果已记录但恢复输入发生变化，需人工复核",
                    needs_manual_review=True,
                )
                self.save_run(group_name, run_date, data)
                return None, data, "result_unknown"
            if (
                operation_status == "succeeded"
                and same_input
                and self.prompt_path(group_name, run_date).is_file()
                and not force
            ):
                return None, data, "already_completed"
            if operation_status == "unknown":
                return None, data, "result_unknown"
            if (
                operation_status == "started"
                and not data.get("prompt_operation_finished_at")
            ):
                data.update(
                    prompt_operation_status="unknown",
                    prompt_hold=True,
                    prompt_hold_reason="PROMPT_RESULT_UNKNOWN",
                    prompt_operation_error="上次 AI 调用已开始但没有可信结果，禁止自动重复调用",
                    needs_manual_review=True,
                )
                self.save_run(group_name, run_date, data)
                return None, data, "result_unknown"

            operation_id = uuid.uuid4().hex
            data.update(
                prompt_operation_id=operation_id,
                prompt_operation_input_hash=input_hash,
                prompt_operation_status="started",
                prompt_operation_started_at=datetime.now().astimezone().isoformat(),
                prompt_operation_finished_at="",
                prompt_operation_error="",
                prompt_operation_result=None,
                prompt_hold=False,
                prompt_hold_reason="",
            )
            self.save_run(group_name, run_date, data)
            return operation_id, data, "claimed"

    def record_prompt_result(
        self,
        group_name: str,
        run_date: str,
        operation_id: str,
        *,
        prompt: str,
        meta: dict | None,
    ) -> dict:
        """在写最终 Prompt 文件前，先持久化已付费调用的结果。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                raise RunStateCorruptionError("运行状态文件损坏，禁止记录 Prompt 结果")
            if data.get("prompt_operation_id") != operation_id:
                raise RuntimeError("Prompt 操作 claim 已失效")
            data.update(
                prompt_operation_status="result_recorded",
                prompt_operation_result={
                    "prompt": prompt,
                    "meta": dict(meta or {}),
                    "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                },
            )
            return self.save_run(group_name, run_date, data)

    def commit_recorded_prompt(
        self,
        group_name: str,
        run_date: str,
        operation_id: str,
    ) -> dict:
        """把已记录结果原子提升为 image_prompt.txt，并完成操作状态。"""
        run_path = self.run_path(group_name, run_date)
        with _run_mutex(run_path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                raise RunStateCorruptionError("运行状态文件损坏，禁止提交 Prompt 结果")
            if data.get("prompt_operation_id") != operation_id:
                raise RuntimeError("Prompt 操作 claim 已失效")
            result = data.get("prompt_operation_result")
            if not isinstance(result, dict) or not isinstance(result.get("prompt"), str):
                raise RuntimeError("Prompt 操作没有可提交的已记录结果")

            prompt = result["prompt"]
            expected_hash = str(result.get("sha256") or "")
            if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != expected_hash:
                raise RunStateCorruptionError("已记录 Prompt 结果哈希不一致")
            prompt_path = self.prompt_path(group_name, run_date)
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = prompt_path.with_name(f".{prompt_path.name}.{operation_id}.tmp")
            temp_path.write_text(prompt, encoding="utf-8")
            os.replace(temp_path, prompt_path)

            data.update(
                status=PROMPT_READY,
                prompt_meta=dict(result.get("meta") or {}),
                prompt_operation_status="succeeded",
                prompt_operation_finished_at=datetime.now().astimezone().isoformat(),
                prompt_operation_result=None,
                prompt_operation_error="",
                prompt_hold=False,
                prompt_hold_reason="",
            )
            return self.save_run(group_name, run_date, data)

    def fail_prompt_operation(
        self,
        group_name: str,
        run_date: str,
        operation_id: str,
        *,
        error: str,
    ) -> dict:
        """记录可确认的失败；该状态可以由显式重试重新领取。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if data.get("prompt_operation_id") != operation_id:
                raise RuntimeError("Prompt 操作 claim 已失效")
            data.update(
                prompt_operation_status="failed",
                prompt_operation_finished_at=datetime.now().astimezone().isoformat(),
                prompt_operation_error=str(error)[:300],
                prompt_operation_result=None,
                prompt_hold=False,
                prompt_hold_reason="",
            )
            return self.save_run(group_name, run_date, data)

    def mark_prompt_result_unknown(
        self,
        group_name: str,
        run_date: str,
        operation_id: str,
        *,
        error: str,
    ) -> dict:
        """提交后结果不明时进入人工 hold，finished_at 故意保持为空。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if data.get("prompt_operation_id") != operation_id:
                raise RuntimeError("Prompt 操作 claim 已失效")
            data.update(
                prompt_operation_status="unknown",
                prompt_operation_error=str(error)[:300],
                prompt_hold=True,
                prompt_hold_reason="PROMPT_RESULT_UNKNOWN",
                needs_manual_review=True,
            )
            return self.save_run(group_name, run_date, data)

    def previous_theme_signature(self, group_name: str, run_date: str) -> str:
        """读取当前日期之前最近一次运行的实际风格签名。"""
        validate_run_date(run_date)
        group_dir = self._group_root(group_name)
        if not group_dir.is_dir():
            return ""
        candidates = sorted(
            (directory for directory in group_dir.iterdir() if directory.is_dir() and directory.name < run_date),
            key=lambda directory: directory.name,
            reverse=True,
        )
        for directory in candidates:
            try:
                validate_run_date(directory.name)
                data = json.loads((directory / FILE_RUN).read_text(encoding="utf-8"))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            meta = data.get("prompt_meta") if isinstance(data, dict) else None
            if isinstance(meta, dict) and meta.get("style_signature"):
                return str(meta["style_signature"])
        return ""

    def recent_layout_history(
        self, group_name: str, run_date: str, *, limit: int = 3
    ) -> tuple[dict[str, str], ...]:
        """只读当前日期之前最近的版式元数据；旧/损坏运行会被安全跳过。"""
        validate_run_date(run_date)
        if limit <= 0:
            return ()
        limit = min(int(limit), 12)
        group_dir = self._group_root(group_name)
        if not group_dir.is_dir():
            return ()
        candidates = sorted(
            (directory for directory in group_dir.iterdir() if directory.is_dir() and directory.name < run_date),
            key=lambda directory: directory.name,
            reverse=True,
        )
        result: list[dict[str, str]] = []
        for directory in candidates:
            try:
                valid_date = validate_run_date(directory.name)
                data = json.loads((directory / FILE_RUN).read_text(encoding="utf-8"))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            meta = data.get("prompt_meta") if isinstance(data, dict) else None
            if not isinstance(meta, dict):
                continue
            layout_id = meta.get("layout_id")
            if not isinstance(layout_id, str) or not layout_id.strip():
                continue
            result.append(
                {
                    "run_date": valid_date,
                    "layout_id": layout_id.strip(),
                    "comedy_device": str(meta.get("comedy_device") or ""),
                    "layout_signature": str(meta.get("layout_signature") or ""),
                }
            )
            if len(result) >= limit:
                break
        return tuple(result)

    # ---------- 微信发送 claim / lease ----------

    def claim_send(
        self,
        group_name: str,
        run_date: str,
        *,
        now: datetime,
        lease_seconds: int,
        allow_hold: bool = False,
        allow_sent: bool = False,
    ) -> tuple[str | None, dict, str]:
        """原子领取发送任务。

        旧 run.json 没有发送字段时按未领取处理。若旧租约过期且存在没有完成
        记录的发送尝试，结果视为未知并转人工复核，禁止自动重复发送。
        """
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                return None, data, "state_corrupt"
            if data.get("sent_at") and not allow_sent:
                return None, data, "already_sent"
            if data.get("send_state") == "unknown":
                return None, data, "result_unknown"
            if data.get("send_hold") and not allow_hold:
                return None, data, "send_hold"

            existing_claim = str(data.get("send_claim_id") or "")
            expires_at = _parse_timestamp(data.get("send_claim_expires_at"), now)
            if existing_claim and expires_at and expires_at > now:
                return None, data, "already_claimed"
            if (existing_claim or _has_unresolved_send_attempt(data)) and _has_unresolved_send_attempt(data):
                data.update(
                    send_state="unknown",
                    send_hold=True,
                    needs_manual_send=True,
                    send_error="上次发送在提交动作后未能确认结果，已暂停自动重试",
                    send_error_type="SEND_RESULT_UNKNOWN",
                    verification_level="unknown",
                    send_claim_id="",
                    send_claimed_at="",
                    send_claim_expires_at="",
                )
                self.save_run(group_name, run_date, data)
                return None, data, "result_unknown"

            claim_id = uuid.uuid4().hex
            claimed_at = now.isoformat()
            data.update(
                send_state="claimed",
                send_claim_id=claim_id,
                send_claimed_at=claimed_at,
                send_claim_expires_at=(now + timedelta(seconds=max(lease_seconds, 30))).isoformat(),
                send_last_attempt_at=claimed_at,
            )
            self.save_run(group_name, run_date, data)
            return claim_id, data, "claimed"

    def update_send_claim(
        self,
        group_name: str,
        run_date: str,
        claim_id: str,
        **fields,
    ) -> tuple[bool, dict]:
        """仅由当前 claim 持有者更新发送状态。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                return False, data
            if data.get("send_claim_id") != claim_id:
                return False, data
            data.update(fields)
            self.save_run(group_name, run_date, data)
            return True, data

    def finish_send_claim(
        self,
        group_name: str,
        run_date: str,
        claim_id: str,
        *,
        send_state: str,
        **fields,
    ) -> tuple[bool, dict]:
        """结束 claim 并原子清理租约。"""
        fields.update(
            send_state=send_state,
            send_claim_id="",
            send_claimed_at="",
            send_claim_expires_at="",
        )
        return self.update_send_claim(group_name, run_date, claim_id, **fields)

    def resolve_text_send_unknown(
        self,
        group_name: str,
        run_date: str,
        *,
        resolution: str,
        expected_send_unknown_at: str,
        now: datetime,
    ) -> tuple[bool, dict, str]:
        """用时间戳 CAS 人工消歧文字提交；本方法本身不执行任何发送。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                return False, data, "state_corrupt"
            if data.get("send_state") != "unknown" or data.get("send_hold_reason") != "SEND_RESULT_UNKNOWN":
                return False, data, "not_unknown"
            if str(data.get("send_unknown_at") or "") != expected_send_unknown_at:
                return False, data, "stale"
            unknown_stage = str(data.get("send_unknown_stage") or "")
            if not unknown_stage:
                unknown_stage = "image" if data.get("image_submitted_at") or data.get("text_sent_at") else "text"
            if unknown_stage != "text":
                return False, data, "unsupported_stage"
            if resolution not in {"text_sent", "not_sent"}:
                return False, data, "invalid_resolution"

            resolved_at = now.isoformat()
            history = list(data.get("send_resolution_history") or [])
            history.append(
                {
                    "stage": "text",
                    "resolution": resolution,
                    "unknown_at": expected_send_unknown_at,
                    "resolved_at": resolved_at,
                }
            )
            common = {
                "send_state": "ready",
                "send_hold": False,
                "send_hold_reason": "",
                "send_error": "",
                "send_error_type": "",
                "send_unknown_at": "",
                "send_unknown_stage": "",
                "send_claim_id": "",
                "send_claimed_at": "",
                "send_claim_expires_at": "",
                "needs_manual_send": True,
                "send_resolution_history": history[-20:],
                "send_last_resolution": resolution,
                "send_last_resolved_at": resolved_at,
            }
            if resolution == "text_sent":
                submitted_at = str(data.get("text_submitted_at") or "")
                if not submitted_at:
                    return False, data, "text_not_submitted"
                data.update(
                    **common,
                    text_attempt_finished_at=resolved_at,
                    text_verified_at=resolved_at,
                    text_sent_at=submitted_at,
                    text_verification_level="manual_ui_observed",
                    verification_level="manual_ui_observed",
                )
            else:
                data.update(
                    **common,
                    text_attempt_started_at="",
                    text_attempt_finished_at="",
                    text_submitted_at="",
                    text_verified_at="",
                    text_sent_at="",
                    text_verification_level="",
                    verification_level="",
                )
            return True, self.save_run(group_name, run_date, data), "resolved"

    def resolve_manual_send(
        self,
        group_name: str,
        run_date: str,
        *,
        resolution: str,
        expected_updated_at: str,
        image_required: bool,
        now: datetime,
    ) -> tuple[bool, dict, str]:
        """用 run.updated_at 做 CAS 写入人工发送结论；绝不执行外部发送。"""
        path = self.run_path(group_name, run_date)
        with _run_mutex(path):
            data = self.load_run(group_name, run_date)
            if self._is_corrupt(data):
                return False, data, "state_corrupt"
            if str(data.get("updated_at") or "") != expected_updated_at:
                return False, data, "stale"
            if data.get("status") not in {IMAGE_READY, READY_TO_SEND}:
                return False, data, "not_resolvable"
            if not data.get("send_hold"):
                return False, data, "not_held"
            if resolution not in {"all_sent", "text_sent", "not_sent"}:
                return False, data, "invalid_resolution"

            ranking_path = self.ranking_txt_path(group_name, run_date)
            if resolution in {"all_sent", "text_sent"} and (
                not ranking_path.exists() or ranking_path.stat().st_size <= 0
            ):
                return False, data, "ranking_missing"
            image_path = self.image_path(group_name, run_date)
            if resolution == "all_sent" and image_required and (
                not image_path.exists() or image_path.stat().st_size <= 0
            ):
                return False, data, "image_missing"

            resolved_at = now.isoformat()
            history = list(data.get("send_resolution_history") or [])
            history.append(
                {
                    "stage": "all" if resolution == "all_sent" else "text" if resolution == "text_sent" else "none",
                    "resolution": resolution,
                    "expected_updated_at": expected_updated_at,
                    "resolved_at": resolved_at,
                    "previous_send_state": str(data.get("send_state") or ""),
                    "previous_send_hold_reason": str(data.get("send_hold_reason") or ""),
                    "previous_send_error": str(data.get("send_error") or data.get("error") or ""),
                }
            )
            common = {
                "send_state": "sent" if resolution == "all_sent" else "ready",
                "send_hold": False,
                "send_hold_reason": "",
                "send_error": "",
                "send_error_type": "",
                "send_unknown_at": "",
                "send_unknown_stage": "",
                "send_claim_id": "",
                "send_claimed_at": "",
                "send_claim_expires_at": "",
                "send_resolution_history": history[-20:],
                "send_last_resolution": resolution,
                "send_last_resolved_at": resolved_at,
                "manual_send_resolution": resolution,
                "manual_send_resolved_at": resolved_at,
            }
            if str(data.get("error_type") or "") == "SEND_RESULT_UNKNOWN":
                common.update(error="", error_type="", failed_stage="")

            if resolution == "all_sent":
                data.update(
                    **common,
                    status=SENT,
                    sent_at=resolved_at,
                    text_attempt_finished_at=resolved_at,
                    text_submitted_at=str(data.get("text_submitted_at") or resolved_at),
                    text_verified_at=resolved_at,
                    text_sent_at=str(data.get("text_sent_at") or resolved_at),
                    text_verification_level="manual_user_confirmed",
                    image_attempt_finished_at=resolved_at if image_required else str(data.get("image_attempt_finished_at") or ""),
                    image_submitted_at=str(data.get("image_submitted_at") or resolved_at) if image_required else "",
                    image_verified_at=resolved_at if image_required else "",
                    image_sent_at=str(data.get("image_sent_at") or resolved_at) if image_required else "",
                    image_verification_level="manual_user_confirmed" if image_required else "",
                    verification_level="manual_user_confirmed",
                    needs_manual_send=False,
                )
            elif resolution == "text_sent":
                data.update(
                    **common,
                    status=READY_TO_SEND,
                    sent_at="",
                    text_attempt_finished_at=resolved_at,
                    text_submitted_at=str(data.get("text_submitted_at") or resolved_at),
                    text_verified_at=resolved_at,
                    text_sent_at=str(data.get("text_sent_at") or resolved_at),
                    text_verification_level="manual_user_confirmed",
                    image_attempt_started_at="",
                    image_attempt_finished_at="",
                    image_submitted_at="",
                    image_verified_at="",
                    image_sent_at="",
                    image_verification_level="",
                    verification_level="manual_user_confirmed",
                    needs_manual_send=True,
                )
            else:
                data.update(
                    **common,
                    status=READY_TO_SEND,
                    sent_at="",
                    text_attempt_started_at="",
                    text_attempt_finished_at="",
                    text_submitted_at="",
                    text_verified_at="",
                    text_sent_at="",
                    text_verification_level="",
                    image_attempt_started_at="",
                    image_attempt_finished_at="",
                    image_submitted_at="",
                    image_verified_at="",
                    image_sent_at="",
                    image_verification_level="",
                    verification_level="",
                    needs_manual_send=True,
                )
            return True, self.save_run(group_name, run_date, data), "resolved"

    def list_runs(self, run_date: str | None = None) -> list[dict]:
        """列出全部已存在的 run（可按日期过滤）。

        run.json 位于 group_dir/<日期>/run.json；未指定日期时遍历每个群的
        所有日期子目录。
        """
        runs: list[dict] = []
        if run_date is not None:
            validate_run_date(run_date)
        if not self.root.exists():
            return runs
        for group_dir in self.root.iterdir():
            if not group_dir.is_dir():
                continue
            if run_date:
                candidates = [group_dir / run_date]
            else:
                candidates = [d for d in group_dir.iterdir() if d.is_dir()]
            for d in candidates:
                run_path = d / FILE_RUN
                if run_path.exists():
                    try:
                        valid_date = validate_run_date(d.name)
                    except ValueError:
                        runs.append(
                            self._corrupt_run(
                                group_dir.name,
                                d.name,
                                run_path,
                                "directory_date_invalid",
                            )
                        )
                        continue
                    runs.append(self._read_run_file(run_path, group_dir.name, valid_date))
        runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return runs
