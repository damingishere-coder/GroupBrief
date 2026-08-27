from sqlalchemy.exc import OperationalError

from app.db.resilience import run_with_sqlite_retry


def test_sqlite_busy_retries_with_finite_budget(monkeypatch):
    calls = 0
    sleeps = []

    def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OperationalError("select 1", {}, Exception("database is locked"))
        return "ok"

    monkeypatch.setattr("app.db.resilience.time.sleep", sleeps.append)

    assert run_with_sqlite_retry(operation, max_attempts=3, base_delay_seconds=0.1) == "ok"
    assert calls == 3
    assert sleeps == [0.1, 0.2]


def test_sqlite_non_busy_error_is_not_retried():
    calls = 0

    def operation():
        nonlocal calls
        calls += 1
        raise OperationalError("select 1", {}, Exception("no such table"))

    try:
        run_with_sqlite_retry(operation, max_attempts=3)
    except OperationalError:
        pass
    else:
        raise AssertionError("expected OperationalError")
    assert calls == 1
