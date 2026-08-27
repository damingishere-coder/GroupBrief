from datetime import date

import pytest

from app.scheduler.period import PeriodResolver


def test_weekday_default_monday_covers_friday_through_sunday():
    window = PeriodResolver().resolve(
        date(2026, 8, 31), schedule_rule="weekday_default"
    )

    assert window.should_run is True
    assert [item.isoformat() for item in window.covered_dates] == [
        "2026-08-28",
        "2026-08-29",
        "2026-08-30",
    ]
    assert window.period_start.date().isoformat() == "2026-08-28"
    assert window.period_end.date().isoformat() == "2026-08-30"


def test_weekday_default_skips_weekend():
    window = PeriodResolver().resolve(
        date(2026, 8, 29), schedule_rule="weekday_default"
    )
    assert window.should_run is False


def test_daily_previous_day_runs_on_weekend():
    window = PeriodResolver().resolve(
        date(2026, 8, 29), schedule_rule="daily_previous_day"
    )
    assert window.should_run is True
    assert [item.isoformat() for item in window.covered_dates] == ["2026-08-28"]


def test_unknown_schedule_rule_is_rejected():
    with pytest.raises(NotImplementedError, match="暂不支持"):
        PeriodResolver().resolve(date(2026, 8, 27), schedule_rule="custom")
