"""Reuse verified daily message snapshots and fetch only gaps in a weekly report."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.ai.speaker_attribution import build_attribution_contract
from app.data_sources.base import DataSourceStatus, FetchResult


def fetch_weekly_messages(*, store, group_name, group_id, start, end,
                          data_source, load_snapshot, timezone):
    tz = ZoneInfo(timezone)

    def local(stamp):
        return (stamp.astimezone(tz) if stamp.tzinfo else stamp).replace(tzinfo=None)

    start, end = local(start), local(end)
    cached = []
    missing = []
    sources = []
    rejected = []
    day = start.date()
    while day <= end.date():
        day_start = max(start, datetime.combine(day, time.min))
        day_end = min(end, datetime.combine(day, time(23, 59, 59)))
        run_date = (day + timedelta(days=1)).isoformat()
        path = store.messages_path(group_name, run_date)
        reused = False
        if path.is_file():
            try:
                run = store.load_run(group_name, run_date)
                if run.get("report_kind") != "daily" or run.get("wechat_group_id") != group_id:
                    raise ValueError("snapshot_group_or_period_mismatch")
                saved_start = local(datetime.fromisoformat(run["message_snapshot_period_start"]))
                saved_end = local(datetime.fromisoformat(run["message_snapshot_period_end"]))
                if saved_start != datetime.combine(day, time.min) or saved_end != datetime.combine(day, time(23, 59, 59)):
                    raise ValueError("snapshot_period_incomplete")
                messages = load_snapshot(path)
                if not messages or any(m.group_id != group_id or not saved_start <= local(m.timestamp) <= saved_end for m in messages):
                    raise ValueError("snapshot_messages_invalid")
                checksum = build_attribution_contract(messages).message_snapshot_sha256
                if not run.get("message_snapshot_sha256") or checksum != run["message_snapshot_sha256"]:
                    raise ValueError("snapshot_hash_mismatch")
                cached.extend(m for m in messages if day_start <= local(m.timestamp) <= day_end)
                sources.append({"date": day.isoformat(), "run_date": run_date,
                                "message_count": len(messages), "sha256": checksum})
                reused = True
            except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
                rejected.append({"date": day.isoformat(), "reason": str(exc)[:160]})
        if not reused:
            if missing and missing[-1][1] + timedelta(seconds=1) == day_start:
                missing[-1] = (missing[-1][0], day_end)
            else:
                missing.append((day_start, day_end))
        day += timedelta(days=1)

    metrics = {"read_strategy": "daily_snapshots_with_gap_fetch", "snapshot_sources": sources,
               "snapshot_message_count": len(cached), "snapshot_rejected": rejected,
               "fetched_ranges": [], "mcp_call_count": 0}
    messages = list(cached)
    for gap_start, gap_end in missing:
        result = data_source.fetch_messages(group_id, gap_start, gap_end)
        meta = result.meta if isinstance(result.meta, dict) else {}
        metrics["mcp_call_count"] += int(meta.get("mcp_call_count") or 0)
        metrics["fetched_ranges"].append({"start": gap_start.isoformat(), "end": gap_end.isoformat(),
                                          "status": result.status.value, "message_count": len(result.messages), "metrics": meta})
        if result.status not in (DataSourceStatus.OK, DataSourceStatus.EMPTY_RESULT):
            return FetchResult([], result.status, result.detail, result.error_type, metrics)
        if result.status == DataSourceStatus.OK and not result.messages:
            return FetchResult([], DataSourceStatus.READ_FAILED, "缺失日期读取返回不一致的空结果", "MESSAGE_FETCH_FAILED", metrics)
        if any(m.group_id != group_id or not gap_start <= local(m.timestamp) <= gap_end for m in result.messages):
            return FetchResult([], DataSourceStatus.READ_FAILED, "补读消息的群或时间范围不匹配", "MESSAGE_FETCH_FAILED", metrics)
        messages.extend(result.messages)
    unique = {}
    for index, message in enumerate(messages):
        unique.setdefault(message.message_id or ("missing-id", index), message)
    messages = sorted(unique.values(), key=lambda m: (local(m.timestamp), m.message_id))
    metrics["message_count"] = len(messages)
    return FetchResult(messages, DataSourceStatus.OK if messages else DataSourceStatus.EMPTY_RESULT, meta=metrics)
