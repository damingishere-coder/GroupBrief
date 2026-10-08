"""V2 群级统计周期解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from app.scheduler.china_calendar import ChinaCalendar, CalendarUnavailableError

# 统计终点：精确到秒的 23:59:59（V2 输出格式要求，不含微秒）
_END_OF_DAY = time(23, 59, 59)
WORKDAYS_WEEKLY_RULE = "workdays_daily_monday_weekly"
CHINA_WORKDAYS_RULE = "china_workdays"


@dataclass
class PeriodWindow:
    """V2 统计周期结果。"""

    run_date: date  # 执行日
    period_start: datetime  # 统计起点（含）
    period_end: datetime  # 统计终点（含）
    should_run: bool  # 今天是否生成
    weekday: int  # 0=周一 ... 6=周日
    rule: str = "daily_previous_day"
    covered_dates: list[date] | None = None
    report_kind: str = "daily"
    top_limit: int = 10
    calendar_version: str = ""
    calendar_error: str = ""
    schedule_override_id: str = ""

    def period_start_str(self) -> str:
        return self.period_start.strftime("%Y-%m-%d %H:%M:%S")

    def period_end_str(self) -> str:
        return self.period_end.strftime("%Y-%m-%d %H:%M:%S")


class PeriodResolver:
    """根据运行日期与群配置的 schedule_rule 计算统计周期。"""

    def __init__(self, calendar: ChinaCalendar | None = None):
        self.calendar = calendar

    def resolve(
        self,
        run_date: date | None = None,
        timezone: str = "Asia/Shanghai",
        schedule_rule: str = "daily_previous_day",
        *,
        group_id: int | None = None,
    ) -> PeriodWindow:
        tz = ZoneInfo(timezone)
        today = run_date or datetime.now(tz).date()
        weekday = today.weekday()

        report_kind = "daily"
        calendar_version = calendar_error = override_id = ""
        if schedule_rule == CHINA_WORKDAYS_RULE:
            calendar = self.calendar or ChinaCalendar()
            targets = [today - timedelta(days=1)]
            should_run = False
            try:
                should_run = calendar.is_workday(today)
                if should_run:
                    start = calendar.previous_workday(today)
                    override = calendar.override(today, group_id)
                    # 休息日只影响执行间隔，不合并进下一份群报。
                    end = start
                    if override:
                        start, end = date.fromisoformat(override["period_start"]), date.fromisoformat(override["period_end"])
                        override_id = override["id"]
                    targets = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
                    report_kind = "multi_day" if len(targets) > 1 else "daily"
                calendar_version = calendar.version
            except CalendarUnavailableError as exc:
                should_run = False
                calendar_error = str(exc)
        elif schedule_rule == WORKDAYS_WEEKLY_RULE:
            targets = [today - timedelta(days=1)]
            should_run = weekday < 5
            if weekday == 0:
                targets = [today - timedelta(days=offset) for offset in range(7, 0, -1)]
                report_kind = "weekly"
        elif schedule_rule in {"daily_previous_day", "daily"}:
            targets = [today - timedelta(days=1)]
            should_run = True
        elif schedule_rule == "weekday_default":
            if weekday >= 5:
                targets = [today - timedelta(days=1)]
                should_run = False
            elif weekday == 0:
                targets = [today - timedelta(days=offset) for offset in (3, 2, 1)]
            else:
                targets = [today - timedelta(days=1)]
            should_run = weekday < 5
        else:
            raise NotImplementedError(f"暂不支持的统计周期规则：{schedule_rule}")

        return PeriodWindow(
            run_date=today,
            period_start=datetime.combine(min(targets), time.min),
            period_end=datetime.combine(max(targets), _END_OF_DAY),
            should_run=should_run,
            weekday=weekday,
            rule=schedule_rule,
            covered_dates=targets,
            report_kind=report_kind,
            top_limit=15 if report_kind == "weekly" else 10,
            calendar_version=calendar_version,
            calendar_error=calendar_error,
            schedule_override_id=override_id,
        )

    def format_dt(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def restore_period(window: PeriodWindow, snapshot: dict) -> PeriodWindow:
    """已存在任务的统计范围不可被当前配置或默认单日窗口覆盖。"""
    if not snapshot.get("period_start") or not snapshot.get("period_end"):
        return window
    start = datetime.fromisoformat(snapshot["period_start"])
    end = datetime.fromisoformat(snapshot["period_end"])
    if end < start:
        raise ValueError("任务统计周期无效")
    kind = str(snapshot.get("report_kind") or "daily")
    return PeriodWindow(
        run_date=window.run_date, period_start=start, period_end=end,
        should_run=window.should_run if window.rule == CHINA_WORKDAYS_RULE else True, weekday=window.weekday,
        rule=str(snapshot.get("schedule_rule") or window.rule),
        covered_dates=[start.date() + timedelta(days=i) for i in range((end.date() - start.date()).days + 1)],
        report_kind=kind, top_limit=int(snapshot.get("top_limit") or (15 if kind == "weekly" else 10)),
        calendar_version=str(snapshot.get("calendar_version") or window.calendar_version),
        calendar_error=window.calendar_error,
        schedule_override_id=str(snapshot.get("schedule_override_id") or window.schedule_override_id),
    )


def automatic_run_allowed(rule: str, run_date: date, now: datetime, snapshot: dict | None = None) -> bool:
    """使用当前群规则拦截自动业务；历史清单不能绕过新规则。"""
    if rule == CHINA_WORKDAYS_RULE:
        if run_date != now.date():
            return False
        if snapshot and snapshot.get("period_start") and snapshot.get("schedule_rule") != CHINA_WORKDAYS_RULE:
            return False
        try:
            group_id = int(snapshot["group_id"]) if snapshot and snapshot.get("group_id") is not None else None
            expected = PeriodResolver().resolve(run_date, schedule_rule=rule, group_id=group_id)
            if not expected.should_run:
                return False
            if snapshot and snapshot.get("period_start"):
                start = datetime.fromisoformat(snapshot["period_start"])
                end = datetime.fromisoformat(snapshot["period_end"])
                days = (end.date() - start.date()).days + 1
                if (not snapshot.get("calendar_version") or not 1 <= days <= 366
                        or start.time() != time.min or end.time() != _END_OF_DAY
                        or snapshot.get("report_kind") != ("multi_day" if days > 1 else "daily")
                        or int(snapshot.get("top_limit", 10)) != 10):
                    return False
                if snapshot.get("schedule_override_id", "") != expected.schedule_override_id:
                    return False
                # 历史范围保留，但旧的假期合并任务不能绕过当前单日统计规则。
                # 日历更新也不能把不符合当前安排的任务自动发出去。
                return (start == expected.period_start and end == expected.period_end
                        and snapshot.get("report_kind") == expected.report_kind)
            return True
        except (TypeError, ValueError, KeyError):
            return False
    if rule != WORKDAYS_WEEKLY_RULE:
        return True
    if now.weekday() >= 5 or run_date.weekday() >= 5:
        return False
    if now.weekday() == 0:
        if run_date != now.date():
            return False
        if snapshot:
            expected = PeriodResolver().resolve(run_date, schedule_rule=rule)
            try:
                return (
                    snapshot.get("report_kind") == "weekly"
                    and datetime.fromisoformat(snapshot["period_start"]) == expected.period_start
                    and datetime.fromisoformat(snapshot["period_end"]) == expected.period_end
                )
            except (KeyError, TypeError, ValueError):
                return False
    return True


def next_run_at(now: datetime, clock: str, rules: list[str]) -> str:
    hour, minute = (int(part) for part in clock.split(":"))
    for offset in range(367):
        candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        windows = [PeriodResolver().resolve(candidate.date(), schedule_rule=rule) for rule in rules]
        if candidate > now and any(window.should_run for window in windows):
            return candidate.isoformat()
        if windows and all(window.calendar_error for window in windows):
            return ""
    return ""
