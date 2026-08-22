"""V1 兼容日期窗口：每天统计前一自然日。"""

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
    is_weekend_summary: bool = False  # 兼容旧接口；每日规则下始终为 False
    weekend_dates: list[date] = None  # 兼容旧接口；仅包含前一自然日


def get_report_window(today: date | None = None, timezone: str = "Asia/Shanghai") -> ReportWindow:
    tz = ZoneInfo(timezone)
    if today is None:
        today = datetime.now(tz).date()

    weekday = today.weekday()  # 0=周一

    target = today - timedelta(days=1)
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
    return f"群报 GroupBrief｜{window.report_date.isoformat()}"


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")
