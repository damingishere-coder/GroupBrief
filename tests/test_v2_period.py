"""V2 P2：每天统计前一自然日。"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.scheduler.period import PeriodResolver

resolver = PeriodResolver()

@pytest.mark.parametrize(
    ("run_date", "target_date"),
    [
        (date(2026, 8, 17), date(2026, 8, 16)),  # 周一 → 周日
        (date(2026, 8, 18), date(2026, 8, 17)),
        (date(2026, 8, 19), date(2026, 8, 18)),
        (date(2026, 8, 20), date(2026, 8, 19)),
        (date(2026, 8, 21), date(2026, 8, 20)),
        (date(2026, 8, 22), date(2026, 8, 21)),  # 周六 → 周五
        (date(2026, 8, 23), date(2026, 8, 22)),  # 周日 → 周六
    ],
)
def test_every_day_covers_only_previous_natural_day(run_date, target_date):
    window = resolver.resolve(run_date=run_date)

    assert window.should_run is True
    assert window.period_start == datetime.combine(target_date, datetime.min.time())
    assert window.period_end == datetime.combine(target_date, datetime.max.time().replace(microsecond=0))
    assert window.covered_dates == [target_date]


def test_unknown_rule_raises():
    with pytest.raises(NotImplementedError):
        resolver.resolve(run_date=date(2026, 8, 18), schedule_rule="unknown_rule")
