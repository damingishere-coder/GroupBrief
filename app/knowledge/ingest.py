"""Immutable archive ingestion. No AI, source reads, or delivery dependencies."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ai.speaker_attribution import build_attribution_contract
from app.knowledge.db import Conflict, canonical, connect, digest, now_iso, transaction

NORMALIZATION_VERSION = "messages-1"


def utc_stamp(value: str, tz: str = "Asia/Shanghai") -> str:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=ZoneInfo(tz))
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("归档路径越界")
    return path


def load_snapshot(root: Path, locator: str, *, tz: str = "Asia/Shanghai") -> dict:
    path = safe_path(root, locator)
    if path.name != "messages.json":
        raise ValueError("仅接受消息归档")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("归档超过单批 64MB 上限，请离线分批导入")
    content = path.read_bytes()
    raw = json.loads(content)
    if not isinstance(raw, list) or any(not isinstance(r, dict) for r in raw):
        raise ValueError("消息归档必须是对象数组")
    run_path = path.with_name("run.json")
    run_bytes = run_path.read_bytes() if run_path.exists() else b"{}"
    run = json.loads(run_bytes)
    if not isinstance(run, dict):
        raise ValueError("运行元数据不是对象")
    if path.read_bytes() != content or (run_path.exists() and run_path.read_bytes() != run_bytes):
        raise Conflict("归档正在变化，下次扫描重试")
    upstream_groups = {str(r.get("group_id") or "") for r in raw}
    upstream_id = str(run.get("wechat_group_id") or "")
    if len(upstream_groups) > 1 or (upstream_id and upstream_groups and upstream_groups != {upstream_id}):
        raise ValueError("消息归档包含不一致的群身份")
    upstream_id = upstream_id or next(iter(upstream_groups), "")
    start = run.get("message_snapshot_period_start") or run.get("period_start")
    end = run.get("message_snapshot_period_end") or run.get("period_end")
    if bool(start) != bool(end):
        raise ValueError("归档统计范围不完整")
    start_utc = utc_stamp(start, tz) if start else None
    end_utc = utc_stamp(end, tz) if end else None
    # Existing archive format uses inclusive end with one-second precision.
    if end_utc:
        end_utc = (datetime.fromisoformat(end_utc) + timedelta(seconds=1)).isoformat(timespec="microseconds")
        if start_utc >= end_utc:
            raise ValueError("归档统计范围无效")
    seen_ids = set()
    for row in raw:
        stamp = utc_stamp(str(row.get("timestamp") or ""), tz)
        if start_utc and not start_utc <= stamp < end_utc:
            raise ValueError("消息超出声明的统计范围")
        if not isinstance(row.get("content", ""), str):
            raise ValueError("消息正文必须是文本")
        if not isinstance(row.get("provenance") or {}, dict):
            raise ValueError("消息来源身份格式无效")
        message_id = str(row.get("message_id") or "")
        if message_id and message_id in seen_ids:
            raise ValueError("单份归档包含重复消息 ID，不能确定消息身份")
        seen_ids.add(message_id)
    expected = str(run.get("message_snapshot_sha256") or "")
    hash_ok = bool(expected and expected == build_attribution_contract(raw).message_snapshot_sha256)
    if expected and not hash_ok:
        raise ValueError("消息快照校验失败")
    fetch_meta = run.get("fetch_metrics") or {}
    if not isinstance(fetch_meta, dict):
        raise ValueError("读取元数据格式无效")
    # An archive hash proves identity, not upstream completeness.
    complete = bool(start_utc and hash_ok and fetch_meta.get("coverage_complete") is True)
    coverage = "complete" if complete and raw else "complete_empty" if complete else "unverified"
    manifest = {
        "locator": locator, "artifact_sha256": digest(content), "local_group_id": run.get("group_id"),
        "upstream_group_id": upstream_id, "range_start": start_utc, "range_end": end_utc,
        "source_scope": str(run.get("source_scope") or "legacy:installation"),
        "coverage_state": coverage, "snapshot_sha256": expected, "fetch_meta": fetch_meta,
        "normalization_version": NORMALIZATION_VERSION,
    }
    return {"manifest": manifest, "rows": raw, "manifest_hash": digest(manifest)}


def resolve_group(con, manifest: dict) -> tuple[int | None, str]:
    local_id = str(manifest.get("local_group_id") or "")
    upstream = manifest["upstream_group_id"]
    if local_id.isdigit():
        row = con.execute("SELECT id,wechat_group_id FROM groups WHERE id=?", (int(local_id),)).fetchone()
        if row and upstream and row["wechat_group_id"] == upstream:
            return row["id"], f"group:{row['id']}"
        # A recorded but missing/mismatched ID is not permission to relink history.
    elif upstream:
        rows = con.execute("SELECT id FROM groups WHERE wechat_group_id=?", (upstream,)).fetchall()
        if len(rows) == 1:
            return rows[0]["id"], f"group:{rows[0]['id']}"
    return None, "orphan:" + digest([local_id, upstream, str(Path(manifest["locator"]).parent.parent)])


def import_snapshot(db_path: Path, snapshot: dict, *, expected_hash: str | None = None,
                    checkpoint=None, fence=None, tz: str = "Asia/Shanghai") -> dict:
    manifest, rows = snapshot["manifest"], snapshot["rows"]
    if expected_hash and snapshot["manifest_hash"] != expected_hash:
        raise Conflict("预览后归档发生变化，请重新预览")
    with connect(db_path, write=True) as con:
        group_id, conversation = resolve_group(con, manifest)
        batch_key = digest([snapshot["manifest_hash"], conversation])
        with transaction(con):
            if fence:
                fence(con)
            batch = con.execute("SELECT * FROM source_batches WHERE batch_key=?", (batch_key,)).fetchone()
            if batch and batch["import_status"] == "complete":
                return {"batch_id": batch["id"], "status": "reused", "record_count": len(rows)}
            if not batch:
                con.execute("""INSERT INTO source_batches(batch_key,group_id,conversation_key,source_scope,
                    source_kind,source_locator,artifact_sha256,manifest_json,range_start,range_end,
                    coverage_state,record_count,fetch_meta_json,import_status,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'staging',?)""",
                    (batch_key, group_id, conversation, manifest["source_scope"], "archive", manifest["locator"],
                     manifest["artifact_sha256"], canonical(manifest), manifest["range_start"], manifest["range_end"],
                     manifest["coverage_state"], len(rows), canonical(manifest["fetch_meta"]), now_iso()))
                batch = con.execute("SELECT * FROM source_batches WHERE batch_key=?", (batch_key,)).fetchone()
        duplicates = Counter()
        new_count = conflict_count = 0
        # Prepare outside transactions. Missing IDs retain multiplicity within a batch.
        prepared = []
        for ordinal, row in enumerate(rows):
            sent_at = utc_stamp(str(row["timestamp"]), tz)
            sender = str(row.get("sender_id") or "")
            content = row.get("content", "")
            kind = str(row.get("message_type") or "text")
            fact_hash = digest([sender, sent_at, kind, content])
            upstream_id = str(row.get("message_id") or "")
            provenance = row.get("provenance") or {}
            if upstream_id:
                identity = "id:" + upstream_id
                identity_kind = "upstream" if provenance.get("identity_kind") == "upstream" else "legacy_unknown"
            else:
                duplicates[fact_hash] += 1
                # Ambiguous fallback IDs do not silently deduplicate overlapping captures.
                identity = f"capture:{batch_key}:{fact_hash}:{duplicates[fact_hash]}"
                identity_kind = "capture_fallback"
            prepared.append((ordinal, row, sent_at, sender, content, kind, fact_hash, upstream_id, identity, identity_kind))
        for offset in range(0, len(prepared), 250):
            if checkpoint:
                checkpoint(offset)
            with transaction(con):
                if fence:
                    fence(con)
                for ordinal, row, sent_at, sender, content, kind, fact_hash, upstream_id, identity, identity_kind in prepared[offset:offset+250]:
                    if con.execute("SELECT 1 FROM message_sources WHERE batch_id=? AND row_ordinal=?", (batch["id"], ordinal)).fetchone():
                        continue
                    message = con.execute("SELECT id,fact_sha256,validation_state FROM messages WHERE conversation_key=? AND source_scope=? AND identity_key=?",
                                          (conversation, manifest["source_scope"], identity)).fetchone()
                    state = "ambiguous" if identity_kind == "capture_fallback" else "valid"
                    if message:
                        message_id = message["id"]
                        if message["validation_state"] == "conflict":
                            state = "conflict"
                        if message["fact_sha256"] != fact_hash:
                            state = "conflict"
                            conflict_count += 1
                            con.execute("UPDATE messages SET validation_state='conflict' WHERE id=?", (message_id,))
                            con.execute("UPDATE source_batches SET coverage_state='conflict' WHERE id IN (SELECT batch_id FROM message_sources WHERE message_id=?)", (message_id,))
                    else:
                        cursor = con.execute("""INSERT INTO messages(group_id,conversation_key,source_scope,identity_key,identity_kind,
                            upstream_message_id,sender_id,sender_name,sender_identity_quality,sent_at,message_type,content,
                            content_sha256,fact_sha256,normalization_version,first_ingested_at,validation_state)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (group_id, conversation, manifest["source_scope"], identity, identity_kind, upstream_id, sender,
                             str(row.get("sender_name") or ""), "reliable" if sender else "name_only", sent_at, kind, content,
                             digest(content.encode("utf-8")), fact_hash, NORMALIZATION_VERSION, now_iso(), state))
                        message_id = cursor.lastrowid
                        new_count += 1
                    con.execute("INSERT INTO message_sources VALUES(?,?,?,?,?,?,?,?)", (
                        batch["id"], ordinal, message_id, conversation, manifest["source_scope"], canonical(row), digest(row), state))
                    if state in {"conflict", "ambiguous"}:
                        con.execute("UPDATE source_batches SET coverage_state=? WHERE id=? AND coverage_state!='conflict'",
                                    ("conflict" if state == "conflict" else "unverified", batch["id"]))
        if checkpoint:
            checkpoint(len(rows))
        with transaction(con):
            if fence:
                fence(con)
            actual = con.execute("SELECT count(*) FROM message_sources WHERE batch_id=?", (batch["id"],)).fetchone()[0]
            if actual != len(rows):
                raise Conflict("来源记录数不一致")
            con.execute("UPDATE source_batches SET import_status='complete' WHERE id=?", (batch["id"],))
        return {"batch_id": batch["id"], "status": "imported", "record_count": len(rows),
                "new_messages": new_count, "conflicts": conflict_count, "group_id": group_id}
