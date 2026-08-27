"""每日预期任务清单。

清单只保存调度与审计所需的脱敏配置快照，不保存聊天、Prompt 或凭据。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from app.db.models import Group
from app.scheduler.period import PeriodResolver

MANIFEST_VERSION = 1
SUPPORTED_SCHEDULE_RULES = frozenset({"weekday_default", "daily_previous_day"})


def build_expected_groups(
    groups: Iterable[Group],
    run_date: date,
    *,
    timezone: str,
    resolver: PeriodResolver | None = None,
) -> list[dict]:
    resolver = resolver or PeriodResolver()
    expected: list[dict] = []
    for group in groups:
        if group.id is None:
            continue
        rule = str(group.schedule_rule or "weekday_default")
        if rule not in SUPPORTED_SCHEDULE_RULES:
            raise ValueError(f"不支持的群级统计周期规则：{rule}")
        window = resolver.resolve(run_date, timezone, schedule_rule=rule)
        if not window.should_run:
            continue
        expected.append(
            {
                "group_id": int(group.id),
                "group_name": str(group.display_name or group.wechat_group_name),
                "wechat_group_id": str(group.wechat_group_id or ""),
                "wechat_group_name": str(group.wechat_group_name or ""),
                "schedule_rule": rule,
                "history_provider_preference": str(group.provider_preference or ""),
                "summary_provider": str(getattr(group, "summary_provider", "") or ""),
                "summary_model": str(group.summary_model or ""),
                "prompt_provider": str(getattr(group, "prompt_provider", "") or ""),
                "prompt_model": str(group.prompt_model or ""),
                "send_time": str(group.send_time or "08:30"),
                "image_enabled": bool(group.image_enabled),
                "ranking_template": str(group.ranking_template or "default"),
                "image_prompt_template": str(group.image_prompt_template or "default"),
                "image_theme": str(group.image_theme or "ai_free"),
                "image_theme_custom": str(group.image_theme_custom or ""),
                "image_prompt_override": str(group.image_prompt_override or ""),
                "send_target": str(group.send_target or ""),
                "wechat_send_enabled": bool(group.wechat_send_enabled),
                "expected_terminal": (
                    "SENT" if group.wechat_send_enabled else "READY_TO_SEND"
                ),
                "period_start": window.period_start.isoformat(),
                "period_end": window.period_end.isoformat(),
            }
        )
    expected.sort(key=lambda item: item["group_id"])
    return expected


def manifest_fields(expected_groups: list[dict]) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_created_at": datetime.now().astimezone().isoformat(),
        "expected_groups": expected_groups,
        "expected_group_count": len(expected_groups),
    }


def expected_group_ids(state: dict) -> list[int]:
    rows = state.get("expected_groups")
    if not isinstance(rows, list):
        return []
    result: list[int] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("group_id")
        if isinstance(value, int) and value > 0:
            result.append(value)
    return result
