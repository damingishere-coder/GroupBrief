"""Read model and explicit archive backfill previews."""
from __future__ import annotations

import base64
import json
from pathlib import Path

from app.knowledge.db import Conflict, canonical, connect, digest
from app.knowledge.ingest import load_snapshot, resolve_group, utc_stamp
from app.knowledge.jobs import enqueue, public_job

VISIBLE = """EXISTS (SELECT 1 FROM message_sources ms JOIN source_batches sb ON sb.id=ms.batch_id
    WHERE ms.message_id=m.id AND sb.import_status='complete')"""


def data_version(con) -> str:
    return digest([tuple(r) for r in con.execute("SELECT id,coverage_state FROM source_batches WHERE import_status='complete' ORDER BY id")])


def visible_where(*, group_id=None, include_deleted=False, include_orphans=False, sender_id=None, start=None, end=None, message_type=None):
    clauses, args = [VISIBLE], []
    if group_id is not None:
        clauses.append("m.group_id=?"); args.append(group_id)
    if not include_orphans:
        clauses.append("m.group_id IS NOT NULL")
    if not include_deleted:
        clauses.append("(m.group_id IS NULL OR EXISTS(SELECT 1 FROM groups g WHERE g.id=m.group_id AND g.deleted_at IS NULL))")
    for column, operator, value in (("sender_id", "=", sender_id), ("sent_at", ">=", start), ("sent_at", "<", end), ("message_type", "=", message_type)):
        if value:
            clauses.append(f"m.{column}{operator}?"); args.append(utc_stamp(value) if column == "sent_at" else value)
    return " AND ".join(clauses), args


def list_messages(path: Path, *, limit=20, cursor=None, **filters) -> dict:
    where, args = visible_where(**filters)
    with connect(path) as con:
        version = data_version(con)
        query_hash = digest(filters)
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if token["version"] != version or token["query"] != query_hash:
                    raise Conflict("数据或筛选已变化，请刷新列表")
                where += " AND (m.sent_at,m.id)>(?,?)"
                args.extend([str(token["time"]), int(token["id"])])
            except (ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, Conflict):
                    raise
                raise ValueError("分页游标无效") from exc
        rows = [dict(r) for r in con.execute(f"SELECT m.* FROM messages m WHERE {where} ORDER BY m.sent_at,m.id LIMIT ?", (*args, limit+1))]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit-1]
            next_cursor = base64.urlsafe_b64encode(canonical({"version": version, "query": query_hash, "time": last["sent_at"], "id": last["id"]}).encode()).decode()
        return {"items": rows[:limit], "next_cursor": next_cursor, "data_version": version}


def message_detail(path: Path, message_id: int) -> dict:
    with connect(path) as con:
        row = con.execute(f"SELECT m.* FROM messages m WHERE m.id=? AND {VISIBLE}", (message_id,)).fetchone()
        if not row:
            raise KeyError(message_id)
        return dict(row)


def message_sources(path: Path, message_id: int, *, limit=20, offset=0) -> dict:
    message_detail(path, message_id)
    with connect(path) as con:
        rows = con.execute("""SELECT ms.*,sb.source_locator,sb.artifact_sha256,sb.range_start,sb.range_end,sb.coverage_state
            FROM message_sources ms JOIN source_batches sb ON sb.id=ms.batch_id
            WHERE ms.message_id=? AND sb.import_status='complete' ORDER BY ms.batch_id,ms.row_ordinal LIMIT ? OFFSET ?""",
            (message_id, limit+1, offset)).fetchall()
        return {"items": [{**dict(r), "raw_record": json.loads(r["raw_record_json"])} for r in rows[:limit]],
                "next_offset": offset+limit if len(rows)>limit else None}


def message_context(path: Path, message_id: int, radius=10) -> dict:
    current = message_detail(path, message_id)
    with connect(path) as con:
        result = []
        for operator, order in (("<", "DESC"), (">", "ASC")):
            rows = [dict(r) for r in con.execute(f"""SELECT m.* FROM messages m WHERE {VISIBLE}
                AND conversation_key=? AND source_scope=? AND (sent_at,id){operator}(?,?)
                ORDER BY sent_at {order},id {order} LIMIT ?""", (current["conversation_key"], current["source_scope"], current["sent_at"], message_id, radius))]
            result.append(list(reversed(rows)) if operator == "<" else rows)
        return {"before": result[0], "message": current, "after": result[1]}


def preview_backfill(path: Path, output: Path, *, group_id=None, start=None, end=None) -> dict:
    items, errors = [], []
    with connect(path) as con:
        for file in sorted(output.glob("*/*/messages.json")):
            locator = file.relative_to(output).as_posix()
            try:
                snapshot = load_snapshot(output, locator)
                group, conversation = resolve_group(con, snapshot["manifest"])
                if group_id is not None and group != group_id:
                    continue
                manifest = snapshot["manifest"]
                # A dated filter must not silently include unknown historical windows.
                if start and (not manifest["range_end"] or manifest["range_end"] <= utc_stamp(start)):
                    continue
                if end and (not manifest["range_start"] or manifest["range_start"] >= utc_stamp(end)):
                    continue
                items.append({"locator": locator, "manifest_hash": snapshot["manifest_hash"], "group_id": group,
                              "conversation_key": conversation, "record_count": len(snapshot["rows"]),
                              "coverage_state": manifest["coverage_state"]})
            except (ValueError, OSError) as exc:
                errors.append({"locator": locator, "error": str(exc)[:200]})
    filters = {"group_id": group_id, "start": start, "end": end}
    return {"version": digest([items, errors, filters]), "filters": filters, "items": items, "errors": errors,
            "record_count": sum(i["record_count"] for i in items), "ai_calls": 0, "send_invoked": False}


def start_backfill(path: Path, output: Path, expected_version: str, **filters) -> dict:
    preview = preview_backfill(path, output, **filters)
    if preview["version"] != expected_version:
        raise Conflict("归档或群归属已变化，请重新预览")
    job = enqueue(path, "import", {"items": preview["items"], "errors": preview["errors"], "preview_version": expected_version}, group_id=filters.get("group_id"))
    return {"job_id": job["id"], "status": job["status"], "ai_calls": 0, "send_invoked": False}


def status(path: Path) -> dict:
    with connect(path) as con:
        return {"available": True, "data_version": data_version(con),
                "message_count": con.execute(f"SELECT count(*) FROM messages m WHERE {VISIBLE}").fetchone()[0],
                "source_count": con.execute("SELECT count(*) FROM source_batches WHERE import_status='complete'").fetchone()[0],
                "coverage": {r[0]: r[1] for r in con.execute("SELECT coverage_state,count(*) FROM source_batches WHERE import_status='complete' GROUP BY coverage_state")},
                "jobs": {r[0]: r[1] for r in con.execute("SELECT status,count(*) FROM knowledge_jobs GROUP BY status")}}


def list_jobs(path: Path, limit=50) -> dict:
    with connect(path) as con:
        return {"items": [public_job(r) for r in con.execute("SELECT * FROM knowledge_jobs ORDER BY id DESC LIMIT ?", (limit,))]}
