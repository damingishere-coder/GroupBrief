"""Government-calendar scheduling tests use local fixtures and no external calls."""
from datetime import date, datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.scheduler.china_calendar import ChinaCalendar, validate_calendar, refresh_calendars, atomic_json, BUNDLED
from app.scheduler.period import PeriodResolver, CHINA_WORKDAYS_RULE, automatic_run_allowed, next_run_at, restore_period
from app.scheduler.task_manifest import build_expected_groups
from app.db.models import Group


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    monkeypatch.setattr("app.scheduler.china_calendar.CACHE", tmp_path)
    return ChinaCalendar(tmp_path)


@pytest.mark.parametrize("run,start,end", [
    ("2026-09-28", "2026-09-24", "2026-09-27"),
    ("2026-09-29", "2026-09-28", "2026-09-28"),
    ("2026-10-08", "2026-09-30", "2026-10-07"),
    ("2026-10-10", "2026-10-09", "2026-10-09"),
    ("2026-10-12", "2026-10-10", "2026-10-11"),
    ("2026-09-21", "2026-09-20", "2026-09-20"),
    ("2026-09-14", "2026-09-11", "2026-09-13"),
    ("2026-02-24", "2026-02-14", "2026-02-23"),
    ("2026-01-04", "2025-12-31", "2026-01-03"),
])
def test_official_windows(calendar, run, start, end):
    window = PeriodResolver(calendar).resolve(date.fromisoformat(run), schedule_rule=CHINA_WORKDAYS_RULE)
    assert window.should_run and not window.calendar_error
    assert window.period_start.date().isoformat() == start
    assert window.period_end.date().isoformat() == end
    assert window.top_limit == 10
    assert window.report_kind == ("multi_day" if start != end else "daily")
    assert window.calendar_version


def test_contiguous_periods_through_full_year(calendar):
    resolver = PeriodResolver(calendar)
    last_end = None
    for offset in range(365):
        day = date(2026, 1, 1) + timedelta(days=offset)
        window = resolver.resolve(day, schedule_rule=CHINA_WORKDAYS_RULE)
        assert not window.calendar_error
        if not window.should_run:
            continue
        if last_end:
            assert window.period_start.date() == last_end + timedelta(days=1)
        last_end = window.period_end.date()


@pytest.mark.parametrize("day", ["2026-09-25", "2026-09-26", "2026-09-27", "2026-10-01", "2026-10-07", "2026-10-11"])
def test_rest_days_block_automatic_work(calendar, day):
    day = date.fromisoformat(day)
    assert not PeriodResolver(calendar).resolve(day, schedule_rule=CHINA_WORKDAYS_RULE).should_run
    assert not automatic_run_allowed(CHINA_WORKDAYS_RULE, day, datetime.combine(day, datetime.min.time()))


def test_exception_is_scoped_and_frozen(calendar):
    override = {"id": "mid-autumn-20260928", "run_date": "2026-09-28", "period_start": "2026-09-25", "period_end": "2026-09-27", "group_ids": [23]}
    atomic_json(calendar.cache_dir / "overrides.json", {"overrides": [override]})
    group = Group(id=23, display_name="测试", wechat_group_name="测试", wechat_group_id="test@chatroom", schedule_rule=CHINA_WORKDAYS_RULE)
    snapshot = build_expected_groups([group], date(2026, 9, 28), timezone="Asia/Shanghai", schedule_send_time="10:00")[0]
    assert snapshot["period_start"] == "2026-09-25T00:00:00"
    assert snapshot["schedule_override_id"] == override["id"]
    assert automatic_run_allowed(CHINA_WORKDAYS_RULE, date(2026, 9, 28), datetime(2026, 9, 28, 10), snapshot)
    wrong = {**snapshot, "period_start": "2026-09-24T00:00:00"}
    assert not automatic_run_allowed(CHINA_WORKDAYS_RULE, date(2026, 9, 28), datetime(2026, 9, 28, 10), wrong)
    assert not automatic_run_allowed(CHINA_WORKDAYS_RULE, date(2026, 9, 28), datetime(2026, 9, 29, 10), snapshot)
    assert PeriodResolver(calendar).resolve(date(2026, 9, 28), schedule_rule=CHINA_WORKDAYS_RULE, group_id=24).period_start.date() == date(2026, 9, 24)
    ordinary = PeriodResolver(calendar).resolve(date(2026, 9, 29), schedule_rule=CHINA_WORKDAYS_RULE, group_id=23)
    assert ordinary.period_start.date() == date(2026, 9, 28) and not ordinary.schedule_override_id
    assert restore_period(ordinary, snapshot).calendar_version == snapshot["calendar_version"]


def test_unknown_calendar_never_uses_weekday_fallback(tmp_path):
    resolver = PeriodResolver(ChinaCalendar(tmp_path, tmp_path))
    window = resolver.resolve(date(2035, 2, 5), schedule_rule=CHINA_WORKDAYS_RULE)
    assert not window.should_run and "2035" in window.calendar_error


def test_long_holiday_next_run(calendar):
    now = datetime(2026, 2, 14, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_run_at(now, "10:00", [CHINA_WORKDAYS_RULE]) == "2026-02-24T10:00:00+08:00"


def test_bad_refresh_preserves_cache(calendar):
    original = json.loads((BUNDLED / "2026.json").read_text(encoding="utf-8"))
    atomic_json(calendar.cache_dir / "2026.json", original)
    before = (calendar.cache_dir / "2026.json").read_bytes()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"year": 2026}))) as client:
        result = refresh_calendars(cache_dir=calendar.cache_dir, today=date(2026, 9, 27), client=client)
    assert "unavailable" in result["years"]["2026"]
    assert (calendar.cache_dir / "2026.json").read_bytes() == before
    assert ChinaCalendar(calendar.cache_dir).is_workday(date(2026, 10, 10))


def test_corrupt_cache_blocks_instead_of_silent_fallback(calendar):
    (calendar.cache_dir / "2026.json").write_text("broken", encoding="utf-8")
    window = PeriodResolver(calendar).resolve(date(2026, 9, 28), schedule_rule=CHINA_WORKDAYS_RULE)
    assert not window.should_run and window.calendar_error


def test_conflicting_next_year_december_entry(calendar):
    data = {"year": 2027, "papers": ["https://www.gov.cn/test"], "days": [{"date": "2026-12-31", "name": "元旦", "isOffDay": True}]}
    atomic_json(calendar.cache_dir / "2027.json", data)
    assert not calendar.is_workday(date(2026, 12, 31))


def test_old_snapshot_does_not_bypass_new_rule(calendar):
    assert not automatic_run_allowed(CHINA_WORKDAYS_RULE, date(2026, 9, 28), datetime(2026, 9, 28, 10),
                                     {"period_start": "2026-09-21T00:00:00", "schedule_rule": "workdays_daily_monday_weekly"})


def test_dashboard_rest_day_and_upcoming_exception(calendar, tmp_path, monkeypatch):
    from app.api import v2_ui_read
    from app.config.settings import Settings
    settings = Settings(_env_file=None, output_root_override=str(tmp_path / "output"))
    group = Group(id=23, display_name="测试", wechat_group_name="测试", wechat_group_id="test@chatroom", schedule_rule=CHINA_WORKDAYS_RULE)
    monkeypatch.setattr(v2_ui_read.repo, "list_groups", lambda *args, **kwargs: [group])
    resting = v2_ui_read.dashboard(session=object(), settings=settings, run_date="2026-09-27")
    assert resting["runtime"]["overall_status"] == "resting"
    assert resting["cards"][0]["status"] == "RESTING"
    assert resting["counts"]["pending"] == 0 and not resting["should_run"]
    atomic_json(calendar.cache_dir / "overrides.json", {"overrides": [{"id": "exception", "run_date": "2026-09-28", "period_start": "2026-09-25", "period_end": "2026-09-27", "group_ids": [23]}]})
    upcoming = v2_ui_read.dashboard(session=object(), settings=settings, run_date="2026-09-28")
    assert upcoming["period_start"] == "2026-09-25 00:00:00"
    assert upcoming["cards"][0]["schedule_override_id"] == "exception"
    assert upcoming["report_kind"] == "multi_day"
