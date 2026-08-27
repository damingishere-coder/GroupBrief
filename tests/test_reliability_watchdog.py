from datetime import datetime
from zoneinfo import ZoneInfo


def test_recovery_dates_are_oldest_first_and_capped_at_thirty():
    from app.scheduler.reliability_watchdog import recovery_dates

    now = datetime(2026, 8, 27, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert recovery_dates(now, 3) == ["2026-08-25", "2026-08-26", "2026-08-27"]
    assert len(recovery_dates(now, 999)) == 30


def test_watchdog_backfills_in_date_order_and_scans_same_dates_for_send(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import reliability_watchdog as watchdog

    settings = Settings(
        _env_file=None,
        reliability_watchdog_enabled=True,
        reliability_lookback_days=3,
    )
    real_state_class = watchdog.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    state.update(
        "2026-08-25",
        generation_started_at="2026-08-25T00:15:00+08:00",
        generation_completed_at="2026-08-25T00:20:00+08:00",
        generation_status="success",
    )
    generated = []
    sent = []

    def fake_daily(run_date, *, settings, skip_email):
        generated.append((run_date, skip_email))
        return {"run_date": run_date, "status": "success"}

    class FakePipeline:
        def __init__(self, settings):
            pass

        def send_due_for_dates(self, run_dates, *, now, recovery):
            sent.append((list(run_dates), recovery))
            return []

    monkeypatch.setattr(watchdog, "DailyScheduleState", TempState)
    monkeypatch.setattr(watchdog, "run_daily_v2_job", fake_daily)
    monkeypatch.setattr(watchdog, "DailyPipeline", FakePipeline)

    now = datetime(2026, 8, 27, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = watchdog.run_reliability_watchdog(settings=settings, now=now)

    assert generated == [("2026-08-26", True), ("2026-08-27", True)]
    assert sent == [(["2026-08-25", "2026-08-26", "2026-08-27"], True)]
    assert result["status"] == "success"


def test_watchdog_does_not_generate_today_before_schedule(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import reliability_watchdog as watchdog

    settings = Settings(
        _env_file=None,
        reliability_watchdog_enabled=True,
        reliability_lookback_days=1,
        schedule_generate_time="08:30",
    )
    calls = []

    class TempState(watchdog.DailyScheduleState):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class FakePipeline:
        def __init__(self, settings):
            pass

        def send_due_for_dates(self, run_dates, *, now, recovery):
            return []

    monkeypatch.setattr(watchdog, "DailyScheduleState", TempState)
    monkeypatch.setattr(watchdog, "DailyPipeline", FakePipeline)
    monkeypatch.setattr(
        watchdog,
        "run_daily_v2_job",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    watchdog.run_reliability_watchdog(
        settings=settings,
        now=datetime(2026, 8, 27, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert calls == []
