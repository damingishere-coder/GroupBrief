"""SMTP 单封邮件幂等交付。

本模块不假设 SMTP 服务端支持 Idempotency-Key。稳定 Message-ID 只用于审计；
真正的本地保护来自逐封 delivery ledger 和提交前状态落盘。
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import smtplib
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator

from app.config.settings import Settings


_DELIVERY_LOCK = threading.RLock()
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80


@dataclass(frozen=True)
class EmailDeliveryIdentity:
    key: str
    message_id: str
    fingerprint: str


@dataclass(frozen=True)
class EmailDeliveryResult:
    status: str
    detail: str = ""
    message_id: str = ""

    @property
    def success(self) -> bool:
        return self.status in {"sent", "already_sent"}

    @property
    def outcome_unknown(self) -> bool:
        return self.status == "unknown"


def _message_fingerprint(message: EmailMessage) -> str:
    """按稳定语义字段计算指纹，避免 MIME 随机 boundary 影响结果。"""
    parts: list[dict[str, str]] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = str(part.get_payload() or "").encode("utf-8", errors="replace")
        parts.append(
            {
                "content_type": part.get_content_type(),
                "filename": str(part.get_filename() or ""),
                "content_disposition": str(part.get_content_disposition() or ""),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    canonical = json.dumps(
        {
            "subject": str(message.get("Subject") or ""),
            "from": str(message.get("From") or ""),
            "to": str(message.get("To") or ""),
            "cc": str(message.get("Cc") or ""),
            "parts": parts,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_email_identity(message: EmailMessage) -> EmailDeliveryIdentity:
    fingerprint = _message_fingerprint(message)
    message_id = f"<groupbrief.{fingerprint[:40]}@localhost>"
    if "Message-ID" in message:
        del message["Message-ID"]
    message["Message-ID"] = message_id
    return EmailDeliveryIdentity(
        key=fingerprint,
        message_id=message_id,
        fingerprint=fingerprint,
    )


@contextmanager
def _delivery_mutex(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    acquired = _DELIVERY_LOCK.acquire(timeout=max(timeout_seconds, 0.1))
    if not acquired:
        raise TimeoutError("等待邮件交付锁超时")
    handle = None
    owns_handle = False
    try:
        if os.name == "nt":
            digest = hashlib.sha256(str(path.resolve()).lower().encode("utf-8")).hexdigest()[:32]
            handle = ctypes.windll.kernel32.CreateMutexW(
                None, False, f"Local\\GroupBrief.Email.{digest}"
            )
            if not handle:
                raise OSError("无法创建邮件交付互斥锁")
            wait_code = ctypes.windll.kernel32.WaitForSingleObject(
                handle, int(timeout_seconds * 1000)
            )
            if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise TimeoutError("等待跨进程邮件交付锁超时")
            owns_handle = True
        yield
    finally:
        if handle:
            if owns_handle:
                ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        _DELIVERY_LOCK.release()


class EmailDeliveryLedger:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path_for(self, identity: EmailDeliveryIdentity) -> Path:
        return self.root / identity.key[:2] / f"{identity.key}.json"

    @staticmethod
    def _read(path: Path) -> tuple[dict, bool]:
        if not path.is_file():
            return {}, False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}, True
        return (data, False) if isinstance(data, dict) else ({}, True)

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def transaction(self, identity: EmailDeliveryIdentity) -> "EmailDeliveryTransaction":
        return EmailDeliveryTransaction(self, identity)


class EmailDeliveryTransaction:
    def __init__(self, ledger: EmailDeliveryLedger, identity: EmailDeliveryIdentity):
        self.ledger = ledger
        self.identity = identity
        self.path = ledger.path_for(identity)
        self.attempt_id = uuid.uuid4().hex
        self.disposition = "prepared"
        self.record: dict = {}
        self._mutex = _delivery_mutex(self.path)
        self._entered = False

    def __enter__(self) -> "EmailDeliveryTransaction":
        self._mutex.__enter__()
        self._entered = True
        try:
            record, corrupt = self.ledger._read(self.path)
            if corrupt:
                self.disposition = "unknown"
                self.record = {
                    "state": "unknown",
                    "error": "邮件交付账本损坏，禁止自动重发",
                }
                return self
            state = str(record.get("state") or "")
            if state == "sent":
                self.disposition = "already_sent"
                self.record = record
                return self
            if state in {"submitting", "unknown"}:
                self.disposition = "unknown"
                self.record = record
                return self

            self.record = {
                "version": 1,
                "state": "prepared",
                "attempt_id": self.attempt_id,
                "message_id": self.identity.message_id,
                "fingerprint": self.identity.fingerprint,
                "prepared_at": datetime.now().astimezone().isoformat(),
                "pid": os.getpid(),
            }
            self.ledger._write(self.path, self.record)
            return self
        except Exception:
            self._mutex.__exit__(None, None, None)
            self._entered = False
            raise

    def _update(self, state: str, **fields) -> None:
        self.record.update(state=state, **fields)
        self.ledger._write(self.path, self.record)
        self.disposition = state

    def mark_prepared(self) -> None:
        self._update("prepared", error="")

    def mark_submitting(self) -> None:
        self._update(
            "submitting",
            submitting_at=datetime.now().astimezone().isoformat(),
            error="",
        )

    def mark_sent(self) -> None:
        self._update(
            "sent",
            sent_at=datetime.now().astimezone().isoformat(),
            error="",
        )

    def mark_failed_before_submit(self, error: str) -> None:
        self._update(
            "failed_before_submit",
            failed_at=datetime.now().astimezone().isoformat(),
            error=str(error)[:300],
        )

    def mark_unknown(self, error: str) -> None:
        self._update(
            "unknown",
            unknown_at=datetime.now().astimezone().isoformat(),
            error=str(error)[:300],
        )

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            if exc is not None and self.disposition == "submitting":
                self.mark_unknown(str(exc))
            elif exc is not None and self.disposition == "prepared":
                self.mark_failed_before_submit(str(exc))
        finally:
            if self._entered:
                self._mutex.__exit__(exc_type, exc, traceback)
                self._entered = False
        return False


def _close_server(server) -> None:
    if server is None:
        return
    try:
        quit_method = getattr(server, "quit", None)
        if quit_method is not None:
            quit_method()
    except Exception:
        # sent 已在 quit 前持久化；关闭连接失败不能触发重复发送。
        pass


def deliver_email(
    message: EmailMessage,
    settings: Settings,
    *,
    ledger: EmailDeliveryLedger | None = None,
    max_attempts: int = 2,
) -> EmailDeliveryResult:
    """发送一封邮件；只重试 send_message 之前的连接/认证失败。"""
    identity = ensure_email_identity(message)
    ledger_root = (
        getattr(settings, "output_dir", None)
        or getattr(settings, "data_dir", None)
        or (Path.cwd() / "output")
    )
    ledger = ledger or EmailDeliveryLedger(Path(ledger_root) / ".email-delivery")

    with ledger.transaction(identity) as transaction:
        if transaction.disposition == "already_sent":
            return EmailDeliveryResult("already_sent", "相同邮件已确认发送，已跳过", identity.message_id)
        if transaction.disposition == "unknown":
            return EmailDeliveryResult(
                "unknown",
                str(transaction.record.get("error") or "上次邮件提交结果未知，禁止自动重发"),
                identity.message_id,
            )

        last_error = ""
        for attempt in range(1, max(1, int(max_attempts)) + 1):
            server = None
            try:
                if settings.email_use_ssl:
                    server = smtplib.SMTP_SSL(
                        settings.email_smtp_host,
                        settings.email_smtp_port,
                        timeout=30,
                    )
                else:
                    server = smtplib.SMTP(
                        settings.email_smtp_host,
                        settings.email_smtp_port,
                        timeout=30,
                    )
                    server.starttls()
                if settings.email_smtp_user:
                    server.login(settings.email_smtp_user, settings.email_smtp_password)
            except Exception as exc:
                last_error = str(exc)[:300]
                transaction.mark_failed_before_submit(last_error)
                _close_server(server)
                if attempt < max_attempts:
                    transaction.mark_prepared()
                    time.sleep(3)
                    continue
                return EmailDeliveryResult(
                    "failed_before_submit", last_error, identity.message_id
                )

            try:
                transaction.mark_submitting()
                refused = server.send_message(message)
                if refused:
                    transaction.mark_unknown("SMTP 返回部分或全部收件人拒绝，结果需人工核对")
                    return EmailDeliveryResult(
                        "unknown",
                        "SMTP 返回部分或全部收件人拒绝，结果需人工核对",
                        identity.message_id,
                    )
                transaction.mark_sent()
                return EmailDeliveryResult("sent", "", identity.message_id)
            except Exception as exc:
                transaction.mark_unknown(str(exc))
                return EmailDeliveryResult("unknown", str(exc)[:300], identity.message_id)
            finally:
                _close_server(server)

        return EmailDeliveryResult("failed_before_submit", last_error, identity.message_id)
