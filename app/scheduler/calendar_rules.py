"""群报日期规则。

- 周一：统计周六 00:00:00 ～ 周日 23:59:59（两天汇总）
- 周二：统计周一 00:00:00 ～ 周一 23:59:59
- 周三：统计周二
- 周四：统计周三
- 周五：统计周四
- 周六：统计周五
- 周日：不执行
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass
class ReportWindow:
    report_date: date  # 报告归属日（执行日）
    range_start: datetime
    range_end: datetime
    should_run: bool
    weekday: int  # 0=周一 ... 6=周日
    is_weekend_summary: bool = False  # 周一的周六+周日汇总
    weekend_dates: list[date] = None  # 周一汇总时包含的日期


def _with_tz(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=tz)


def get_report_window(today: date | None = None, timezone: str = "Asia/Shanghai") -> ReportWindow:
    tz = ZoneInfo(timezone)
    if today is None:
        today = datetime.now(tz).date()

    weekday = today.weekday()  # 0=周一

    if weekday == 6:  # 周日：不执行
        return ReportWindow(
            report_date=today,
            range_start=datetime.combine(today, time.min),
            range_end=datetime.combine(today, time.max),
            should_run=False,
            weekday=weekday,
        )

    if weekday == 0:  # 周一：周六 00:00 ～ 周日 23:59
        saturday = today - timedelta(days=2)
        sunday = today - timedelta(days=1)
        return ReportWindow(
            report_date=today,
            range_start=datetime.combine(saturday, time.min),
            range_end=datetime.combine(sunday, time.max),
            should_run=True,
            weekday=weekday,
            is_weekend_summary=True,
            weekend_dates=[saturday, sunday],
        )

    target = today - timedelta(days=1)  # 其余：统计前一天
    return ReportWindow(
        report_date=today,
        range_start=datetime.combine(target, time.min),
        range_end=datetime.combine(target, time.max),
        should_run=True,
        weekday=weekday,
        weekend_dates=[target],
    )


def email_subject(window: ReportWindow) -> str:
    """邮件主题。"""
    if window.is_weekend_summary:
        return (
            f"群报 GroupBrief｜周末汇总｜"
            f"{window.range_start.date().isoformat()}～{window.range_end.date().isoformat()}"
        )
    return f"群报 GroupBrief｜{window.report_date.isoformat()}"


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")
