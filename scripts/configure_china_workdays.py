"""Preview/apply the approved six-group workday migration while the service is stopped."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys

from app.db.models import Group
from app.scheduler.china_calendar import ChinaCalendar, atomic_json
from app.scheduler.daily_v2_job import DailyScheduleState
from app.scheduler.period import CHINA_WORKDAYS_RULE, PeriodResolver
from app.scheduler.task_manifest import build_expected_groups, manifest_fields

TARGETS = {23: "48643066777@chatroom", 24: "54409439719@chatroom", 25: "45638125048@chatroom",
           26: "44726152571@chatroom", 27: "43058033720@chatroom", 28: "9439243003@chatroom"}
OVERRIDE = {"id": "mid-autumn-20260928", "run_date": "2026-09-28", "period_start": "2026-09-25",
            "period_end": "2026-09-27", "group_ids": sorted(TARGETS),
            "reason": "用户确认：仅本次汇总中秋三天，不包含24日，不补发历史任务"}


def configure(database: Path, calendar_dir: Path, output_dir: Path, *, apply: bool = False) -> dict:
    database = database.resolve(strict=True)
    overrides_path = calendar_dir / "overrides.json"
    state_store = DailyScheduleState(output_dir)
    run_date = OVERRIDE["run_date"]
    state_path = output_dir / ".scheduler" / f"{run_date}.json"
    with sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM groups WHERE enabled=1 AND deleted_at IS NULL ORDER BY id")]
        if [row["id"] for row in rows] != sorted(TARGETS) or any(row["wechat_group_id"] != TARGETS[row["id"]] for row in rows):
            raise ValueError("启用群与已确认的六群不一致，停止切换")
        clocks = dict(conn.execute("SELECT key,value FROM settings WHERE key IN ('schedule_generate_time','schedule_send_time')"))
        if clocks != {"schedule_generate_time": "00:15", "schedule_send_time": "10:00"}:
            raise ValueError("实际生成/发送时间与已确认安排不一致")
        old_overrides = overrides_path.read_bytes() if overrides_path.exists() else None
        old_state = state_path.read_bytes() if state_path.exists() else None
        data = json.loads(old_overrides) if old_overrides is not None else {"overrides": []}
        related = [row for row in data["overrides"] if row.get("run_date") == run_date and set(row.get("group_ids", [])) & set(TARGETS)]
        if related and related != [OVERRIDE]:
            raise ValueError("已存在不同的临时安排，停止覆盖")
        new_overrides = data if related else {**data, "overrides": [*data["overrides"], OVERRIDE]}
        existing = state_store.load(run_date)
        if old_state:
            manifest = existing.get("expected_groups", [])
            if existing.get("state_status") == "corrupt" or sorted(row.get("group_id") for row in manifest) != sorted(TARGETS) or any(
                row.get("schedule_override_id") != OVERRIDE["id"] or row.get("period_start") != "2026-09-25T00:00:00"
                or row.get("period_end") != "2026-09-27T23:59:59" or row.get("schedule_rule") != CHINA_WORKDAYS_RULE for row in manifest
            ):
                raise ValueError("28日已有不同任务清单，停止覆盖")
        # Never repurpose an already generated report as the temporary combined report.
        for path in output_dir.glob(f"*/{run_date}/run.json"):
            run = json.loads(path.read_text(encoding="utf-8"))
            if int(run.get("group_id") or 0) in TARGETS and run.get("schedule_override_id") != OVERRIDE["id"]:
                raise ValueError("28日已有其他统计范围的群报，停止覆盖")
        result = {"applied": False, "groups": [{"id": row["id"], "name": row["display_name"], "old_rule": row["schedule_rule"]} for row in rows],
                  "new_rule": CHINA_WORKDAYS_RULE, "override": OVERRIDE, "send_time": "10:00"}
        if not apply:
            return result
        if related and old_state and all(row["schedule_rule"] == CHINA_WORKDAYS_RULE for row in rows):
            return {**result, "already_applied": True}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.parent / "backups" / f"china-workdays-{stamp}"
        backup.mkdir(parents=True)
        with sqlite3.connect(backup / database.name) as copy:
            conn.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("数据库备份校验失败")
        if calendar_dir.exists():
            shutil.copytree(calendar_dir, backup / "work_calendar")
        if (output_dir / ".scheduler").exists():
            shutil.copytree(output_dir / ".scheduler", backup / "scheduler")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                changed = conn.execute("UPDATE groups SET schedule_rule=?,updated_at=? WHERE id=? AND enabled=1 AND schedule_rule=?",
                                       (CHINA_WORKDAYS_RULE, datetime.now(timezone.utc).isoformat(), row["id"], row["schedule_rule"]))
                if changed.rowcount != 1:
                    raise ValueError("预览后群配置已变化")
            atomic_json(overrides_path, new_overrides)
            resolver = PeriodResolver(ChinaCalendar(calendar_dir))
            groups = [Group.model_validate({**row, "schedule_rule": CHINA_WORKDAYS_RULE}) for row in rows]
            manifest = build_expected_groups(groups, date.fromisoformat(run_date), timezone="Asia/Shanghai", schedule_send_time="10:00", resolver=resolver)
            if len(manifest) != len(TARGETS) or any(row["schedule_override_id"] != OVERRIDE["id"] for row in manifest):
                raise ValueError("临时任务清单校验失败")
            if not old_state:
                state_store.update(run_date, **manifest_fields(manifest), manifest_source="approved_one_off", generation_status="not_started")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("切换后数据库校验失败")
            conn.commit()
        except Exception:
            conn.rollback()
            for path, old in ((overrides_path, old_overrides), (state_path, old_state)):
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(old)
            raise
        result.update(applied=True, backup=str(backup))
        atomic_json(backup / "receipt.json", result)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--calendar-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(args.database, args.calendar_dir, args.output_dir, apply=args.apply), ensure_ascii=False, indent=2))
