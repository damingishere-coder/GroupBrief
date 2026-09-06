"""V2 群级统计周期解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# 统计终点：精确到秒的 23:59:59（V2 输出格式要求，不含微秒）
_END_OF_DAY = time(23, 59, 59)
WORKDAYS_WEEKLY_RULE = "workdays_daily_monday_weekly"


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

    def period_start_str(self) -> str:
        return self.period_start.strftime("%Y-%m-%d %H:%M:%S")

    def period_end_str(self) -> str:
        return self.period_end.strftime("%Y-%m-%d %H:%M:%S")


class PeriodResolver:
    """根据运行日期与群配置的 schedule_rule 计算统计周期。"""

    def resolve(
        self,
        run_date: date | None = None,
        timezone: str = "Asia/Shanghai",
        schedule_rule: str = "daily_previous_day",
    ) -> PeriodWindow:
        tz = ZoneInfo(timezone)
        today = run_date or datetime.now(tz).date()
        weekday = today.weekday()

        report_kind = "daily"
        if schedule_rule == WORKDAYS_WEEKLY_RULE:
            targets = [today - timedelta(days=1)]
            should_run = weekday < 5
            if weekday == 0:
                targets = [today - timedelta(days=offset) for offset in range(7, 0, -1)]
                report_kind = "weekly"
        elif schedule_rule == "daily_previous_day":
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
        should_run=True, weekday=window.weekday,
        rule=str(snapshot.get("schedule_rule") or window.rule),
        covered_dates=[start.date() + timedelta(days=i) for i in range((end.date() - start.date()).days + 1)],
        report_kind=kind, top_limit=int(snapshot.get("top_limit") or (15 if kind == "weekly" else 10)),
    )


def automatic_run_allowed(rule: str, run_date: date, now: datetime, snapshot: dict | None = None) -> bool:
    """使用当前群规则拦截自动业务；历史清单不能绕过新规则。"""
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
    for offset in range(8):
        candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now and any(PeriodResolver().resolve(candidate.date(), schedule_rule=rule).should_run for rule in rules):
            return candidate.isoformat()
    return ""
