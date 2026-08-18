"""V2 P2：统计周期解析器单元测试。

默认规则：
- 周一：周五 00:00:00 ～ 周日 23:59:59（三天）
- 周二~周五：前一天
- 周六 / 周日：不生成
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.scheduler.period import PeriodResolver

resolver = PeriodResolver()

# 2026-08-14 周五 / 08-17 周一 / 08-18 周二 / 08-19 周三 /
# 08-20 周四 / 08-21 周五 / 08-22 周六 / 08-23 周日


def test_monday_covers_three_days():
    w = resolver.resolve(run_date=date(2026, 8, 17))  # 周一
    assert w.should_run is True
    assert w.weekday == 0
    assert w.period_start == datetime(2026, 8, 14, 0, 0, 0)  # 周五
    assert w.period_end == datetime(2026, 8, 16, 23, 59, 59)  # 周日
    assert len(w.covered_dates) == 3
    assert w.period_start_str() == "2026-08-14 00:00:00"
    assert w.period_end_str() == "2026-08-16 23:59:59"


def test_tuesday_previous_day():
    w = resolver.resolve(run_date=date(2026, 8, 18))  # 周二
    assert w.should_run is True
    assert w.period_start == datetime(2026, 8, 17, 0, 0, 0)
    assert w.period_end == datetime(2026, 8, 17, 23, 59, 59)
    assert len(w.covered_dates) == 1


def test_wednesday_previous_day():
    w = resolver.resolve(run_date=date(2026, 8, 19))
    assert w.should_run is True
    assert w.period_start == datetime(2026, 8, 18, 0, 0, 0)
    assert w.period_end == datetime(2026, 8, 18, 23, 59, 59)


def test_thursday_previous_day():
    w = resolver.resolve(run_date=date(2026, 8, 20))
    assert w.should_run is True
    assert w.period_start == datetime(2026, 8, 19, 0, 0, 0)


def test_friday_previous_day():
    w = resolver.resolve(run_date=date(2026, 8, 21))
    assert w.should_run is True
    assert w.period_start == datetime(2026, 8, 20, 0, 0, 0)


def test_saturday_skipped():
    w = resolver.resolve(run_date=date(2026, 8, 22))  # 周六
    assert w.should_run is False
    assert w.weekday == 5


def test_sunday_skipped():
    w = resolver.resolve(run_date=date(2026, 8, 23))  # 周日
    assert w.should_run is False
    assert w.weekday == 6


def test_unknown_rule_raises():
    with pytest.raises(NotImplementedError):
        resolver.resolve(run_date=date(2026, 8, 18), schedule_rule="unknown_rule")
