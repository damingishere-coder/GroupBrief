from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.repair.store import RepairIncidentStore, repair_mode_for


def _settings(tmp_path, **updates):
    return Settings(
        _env_file=None,
        output_root_override=str(tmp_path / "output"),
        repair_enabled=True,
        **updates,
    )


def test_incident_is_redacted_and_deduplicated_for_seven_days(tmp_path):
    store = RepairIncidentStore(_settings(tmp_path))
    now = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = store.record(
        scope="ranking",
        error_type="RANKING_FAILED",
        stage="render",
        source_path="output/group/run.json",
        error_summary="token=secret-value render failed",
        now=now,
    )
    second = store.record(
        scope="ranking",
        error_type="RANKING_FAILED",
        stage="render",
        source_path="another/location.json",
        error_summary="token=secret-value render failed",
        now=now + timedelta(days=1),
    )

    assert first["incident_id"] == second["incident_id"]
    assert second["occurrence_count"] == 2
    assert "secret-value" not in second["redacted_error_summary"]


def test_unknown_external_and_recursive_incidents_never_enter_code_fix_queue(tmp_path):
    store = RepairIncidentStore(_settings(tmp_path))
    unknown = store.record(
        scope="send",
        error_type="SEND_RESULT_UNKNOWN",
        stage="submit",
        source_path="run.json",
    )
    recursive = store.record(
        scope="repair",
        error_type="CODEX_EXEC_FAILED",
        stage="controller",
        source_path="controller.json",
    )

    assert unknown["status"] == "diagnostic_only"
    assert recursive["status"] == "diagnostic_only"
    assert repair_mode_for("wechat", "SEND_TEXT_FAILED") == "environment"


def test_daily_limit_and_three_failures_open_circuit_for_24_hours(tmp_path):
    settings = _settings(tmp_path, repair_max_per_day=3)
    store = RepairIncidentStore(settings)
    now = datetime(2026, 9, 5, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    for index in range(3):
        store.record(
            scope="ranking",
            error_type=f"RANKING_FAILED_{index}",
            stage="render",
            source_path=f"run-{index}.json",
            now=now,
        )
        incident, reason = store.start_next(now=now + timedelta(minutes=index))
        assert reason == "started"
        store.finish(incident, success=False, reason="test failure", now=now + timedelta(minutes=index))

    incident, reason = store.start_next(now=now + timedelta(minutes=4))
    assert incident is None
    assert reason == "circuit_open"
    summary = store.summary()
    assert summary["circuit_open"] is True
    assert datetime.fromisoformat(summary["circuit_until"]) >= now + timedelta(hours=23)


def test_daily_limit_stops_third_code_repair_attempt(tmp_path):
    settings = _settings(
        tmp_path,
        repair_max_per_day=2,
        repair_circuit_failure_threshold=99,
    )
    store = RepairIncidentStore(settings)
    now = datetime(2026, 9, 5, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    for index in range(3):
        store.record(
            scope="ranking",
            error_type=f"DAILY_LIMIT_{index}",
            stage="render",
            source_path=f"run-{index}.json",
            now=now,
        )
    for index in range(2):
        incident, reason = store.start_next(now=now + timedelta(minutes=index))
        assert reason == "started"
        store.finish(incident, success=False, now=now + timedelta(minutes=index))

    incident, reason = store.start_next(now=now + timedelta(minutes=3))
    assert incident is None
    assert reason == "daily_limit"
