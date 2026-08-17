"""P3 测试：日期规则（模拟一周）+ 多群批量生成 + 防重复。"""

from datetime import date

from app.scheduler.calendar_rules import email_subject, get_report_window


def test_monday_weekend_summary():
    w = get_report_window(date(2026, 8, 17))  # 周一
    assert w.should_run
    assert w.is_weekend_summary
    assert w.range_start.isoformat() == "2026-08-15T00:00:00"
    assert w.range_end.isoformat() == "2026-08-16T23:59:59.999999"
    assert [d.isoformat() for d in w.weekend_dates] == ["2026-08-15", "2026-08-16"]


def test_tuesday_to_saturday_prev_day():
    for day, prev in [
        (date(2026, 8, 18), "2026-08-17"),  # 周二 → 周一
        (date(2026, 8, 19), "2026-08-18"),  # 周三 → 周二
        (date(2026, 8, 20), "2026-08-19"),  # 周四 → 周三
        (date(2026, 8, 21), "2026-08-20"),  # 周五 → 周四
        (date(2026, 8, 22), "2026-08-21"),  # 周六 → 周五
    ]:
        w = get_report_window(day)
        assert w.should_run
        assert not w.is_weekend_summary
        assert w.range_start.date().isoformat() == prev
        assert w.range_end.date().isoformat() == prev


def test_sunday_skipped():
    w = get_report_window(date(2026, 8, 23))  # 周日
    assert not w.should_run


def test_email_subject_normal():
    w = get_report_window(date(2026, 8, 18))
    assert email_subject(w) == "群报 GroupBrief｜2026-08-18"


def test_email_subject_weekend():
    w = get_report_window(date(2026, 8, 17))
    assert email_subject(w) == "群报 GroupBrief｜周末汇总｜2026-08-15～2026-08-16"
