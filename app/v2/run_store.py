"""V2 运行状态存储：output/<群名称>/<日期>/run.json。

run.json 是每个群每次运行的唯一状态文件（路线文档 §十）。
状态机：PENDING → DATA_READY → RANKING_READY → PROMPT_READY →
IMAGE_READY → READY_TO_SEND → SENT / FAILED。

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
    FILE_IMAGE,
    FILE_IMAGE_PREVIOUS,
    FILE_IMAGE_REGENERATING,
    FILE_MESSAGES,
    FILE_PROMPT,
    FILE_PROMPT_ORIGINAL,
    FILE_RANKING_JSON,
    FILE_RANKING_TXT,
    FILE_RUN,
    PENDING,
)


_RUN_WRITE_LOCK = threading.RLock()
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80


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

    def load_run(self, group_name: str, run_date: str) -> dict:
        path = self.run_path(group_name, run_date)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"group_name": group_name, "run_date": run_date, "status": PENDING}

    def save_run(self, group_name: str, run_date: str, data: dict) -> dict:
        with _RUN_WRITE_LOCK:
            path = self.run_path(group_name, run_date)
            path.parent.mkdir(parents=True, exist_ok=True)
            data.setdefault("group_name", group_name)
            data["run_date"] = run_date
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
            return data

    def update(self, group_name: str, run_date: str, **fields) -> dict:
        """加载 → 合并字段 → 保存，返回最新 run。"""
        with _RUN_WRITE_LOCK:
            data = self.load_run(group_name, run_date)
            data.update(fields)
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
                        runs.append(json.loads(run_path.read_text(encoding="utf-8")))
                    except (json.JSONDecodeError, OSError):
                        continue
        runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return runs
