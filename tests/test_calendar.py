"""P3 测试：V1 兼容链路也每天统计前一自然日。"""

from datetime import date

from app.scheduler.calendar_rules import email_subject, get_report_window


def test_monday_to_sunday_previous_day():
    for day, prev in [
        (date(2026, 8, 17), "2026-08-16"),  # 周一 → 周日
        (date(2026, 8, 18), "2026-08-17"),  # 周二 → 周一
        (date(2026, 8, 19), "2026-08-18"),  # 周三 → 周二
        (date(2026, 8, 20), "2026-08-19"),  # 周四 → 周三
        (date(2026, 8, 21), "2026-08-20"),  # 周五 → 周四
        (date(2026, 8, 22), "2026-08-21"),  # 周六 → 周五
        (date(2026, 8, 23), "2026-08-22"),  # 周日 → 周六
    ]:
        w = get_report_window(day)
        assert w.should_run
        assert not w.is_weekend_summary
        assert w.range_start.date().isoformat() == prev
        assert w.range_end.date().isoformat() == prev
        assert [d.isoformat() for d in w.weekend_dates] == [prev]


def test_email_subject_normal():
    w = get_report_window(date(2026, 8, 18))
    assert email_subject(w) == "群报 GroupBrief｜2026-08-18"


def test_email_subject_monday_has_no_weekend_summary():
    w = get_report_window(date(2026, 8, 17))
    assert email_subject(w) == "群报 GroupBrief｜2026-08-17"
