"""Optional child worker, managed by the existing scheduler owner. No sender."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config.settings import get_settings
from app.knowledge.db import Conflict, KnowledgeUnavailable, connect
from app.knowledge.ingest import import_snapshot, load_snapshot
from app.knowledge.jobs import LeaseLost, assert_owner, claim, enqueue, finish, heartbeat

logger = logging.getLogger(__name__)


def process_one(settings) -> bool:
    if shutil.disk_usage(settings.db_path.parent).free < settings.knowledge_min_free_bytes:
        return False
    job = claim(settings.db_path, f"{os.getpid()}:{uuid.uuid4().hex}")
    if not job:
        return False
    try:
        if job['job_kind']=='index':
            from app.knowledge.search import build_index
            result=build_index(settings.db_path,settings.output_dir,rebuild=json.loads(job['scope_json']).get('rebuild',False),
                               fence=lambda con:assert_owner(con,job),checkpoint=lambda:heartbeat(settings.db_path,job,{}))
            finish(settings.db_path,job,'SUCCEEDED',result)
            return True
        if job['job_kind']=='capture':
            from app.knowledge.capture import PrimaryBusy, capture_day
            from app.knowledge.jobs import defer
            try:
                result=capture_day(settings,job)
            except PrimaryBusy:
                defer(settings.db_path,job,'PRIMARY_BUSY')
                return True
            finish(settings.db_path,job,'SUCCEEDED',result)
            return True
        if job["job_kind"] == "insight":
            from app.knowledge.insights import build
            scope = json.loads(job['scope_json'])
            heartbeat(settings.db_path, job, {})
            result = build(settings.db_path, scope['group_id'], scope['kind'], scope['day'],
                           settings.app_timezone, fence=lambda con: assert_owner(con, job))
            finish(settings.db_path, job, 'SUCCEEDED', result)
            return True
        if job["job_kind"] != "import":
            raise ValueError("不支持的知识任务类型")
        scope = json.loads(job["scope_json"])
        saved = json.loads(job["checkpoint_json"])
        results = saved.get("results", [])
        errors = saved.get("errors", list(scope.get("errors", [])))
        for index, item in enumerate(scope["items"]):
            if index < int(saved.get("next_item", 0)):
                continue
            def checkpoint(row_offset):
                heartbeat(settings.db_path, job, {"next_item": index, "row_offset": row_offset, "results": results, "errors": errors})
            checkpoint(0)
            try:
                snapshot = load_snapshot(settings.output_dir, item["locator"], tz=settings.app_timezone)
                with connect(settings.db_path) as con:
                    from app.knowledge.ingest import resolve_group
                    if resolve_group(con, snapshot["manifest"])[1] != item["conversation_key"]:
                        raise ValueError("预览后群归属发生变化，请重新预览")
                result = import_snapshot(settings.db_path, snapshot, expected_hash=item["manifest_hash"],
                                         checkpoint=checkpoint, fence=lambda con: assert_owner(con, job), tz=settings.app_timezone)
                results.append(result)
            except LeaseLost:
                raise
            except (ValueError, OSError) as exc:
                errors.append({"locator": item["locator"], "error": str(exc)[:200]})
            heartbeat(settings.db_path, job, {"next_item": index+1, "results": results, "errors": errors})
        finish(settings.db_path, job, "PARTIAL" if errors else "SUCCEEDED", {"items": results, "errors": errors})
    except InterruptedError:
        finish(settings.db_path, job, "PAUSED", {"detail": "已在批次边界暂停"})
    except LeaseLost:
        # A superseded owner must not change the job's status.
        logger.info("任务租约变化，旧进程放弃提交", exc_info=True)
    except KnowledgeUnavailable:
        # Local-only jobs can safely recover after their lease expires.
        logger.warning("知识库写入暂不可用，等待租约恢复", exc_info=True)
    except Exception as exc:
        finish(settings.db_path, job, "FAILED", {"error": str(exc)[:300]}, type(exc).__name__)
    return True


def scan_recent(settings) -> None:
    """Bounded scan of seven recent run directories; explicit backfill does older data."""
    today = datetime.now(ZoneInfo(settings.app_timezone)).date()
    allowed = {int(value.strip()) for value in settings.knowledge_group_ids.split(',') if value.strip()}
    if not allowed:
        return
    items = []
    with connect(settings.db_path) as con:
        from app.knowledge.ingest import resolve_group
        for folder in settings.output_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            for offset in range(8):
                path = folder / (today-timedelta(days=offset)).isoformat() / "messages.json"
                if not path.is_file():
                    continue
                try:
                    locator = path.relative_to(settings.output_dir).as_posix()
                    snapshot = load_snapshot(settings.output_dir, locator, tz=settings.app_timezone)
                    group_id, conversation = resolve_group(con, snapshot["manifest"])
                    if group_id not in allowed:
                        continue
                    items.append({"locator": locator, "manifest_hash": snapshot["manifest_hash"], "group_id": group_id,
                                  "conversation_key": conversation})
                except (ValueError, OSError):
                    logger.warning("跳过无效或正在变化的快照：%s", path.name)
    if items:
        enqueue(settings.db_path, "import", {"items": items, "errors": []}, priority=1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    settings = get_settings()
    if not settings.knowledge_enabled:
        return
    last_scan = 0.0
    while True:
        if args.parent_pid and not parent_alive(args.parent_pid):
            return
        try:
            if time.monotonic() - last_scan > 300:
                scan_recent(settings)
                from app.knowledge.capture import schedule_knowledge
                schedule_knowledge(settings)
                with connect(settings.db_path) as con:
                    search_ready=con.execute("SELECT 1 FROM sqlite_master WHERE name='search_state'").fetchone()
                if search_ready:
                    from app.knowledge.search import enqueue_index
                    enqueue_index(settings.db_path,settings.output_dir)
                last_scan = time.monotonic()
            process_one(settings)
        except Exception:
            logger.exception("知识 worker 暂停本轮；不影响日报")
        if args.once:
            return
        time.sleep(5)


def parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)) and code.value == 259)
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
