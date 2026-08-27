from datetime import datetime, timedelta, timezone

from app.v2.constants import (
    EXECUTION_COMPLETE,
    EXECUTION_FAILED_FINAL,
    EXECUTION_HOLD_MANUAL,
    EXECUTION_WAIT_RETRY,
    FAILED,
    MESSAGE_FETCH_FAILED,
    SENT,
)
from app.v2.reliability import enrich_run_state, retry_is_due
from app.v2.run_store import RunStore


def test_retryable_failure_gets_checkpoint_budget_and_due_time():
    now = datetime(2026, 8, 27, 0, 15, tzinfo=timezone.utc)
    run = enrich_run_state(
        {
            "group_name": "群A",
            "run_date": "2026-08-27",
            "status": FAILED,
            "failed_stage": "data",
            "error_type": MESSAGE_FETCH_FAILED,
            "error": "temporary timeout",
        },
        now=now,
    )

    assert run["execution_state"] == EXECUTION_WAIT_RETRY
    assert run["retry_attempt_count"] == 1
    assert run["retry_budget"] == 3
    assert run["next_retry_at"] == (now + timedelta(seconds=60)).isoformat()
    assert retry_is_due(run, now=now) is False
    assert retry_is_due(run, now=now + timedelta(seconds=60)) is True


def test_same_failure_snapshot_does_not_consume_retry_budget_twice():
    now = datetime(2026, 8, 27, 0, 15, tzinfo=timezone.utc)
    first = enrich_run_state(
        {
            "group_name": "群A",
            "run_date": "2026-08-27",
            "status": FAILED,
            "failed_stage": "prompt",
            "error_type": "PROMPT_FAILED",
            "error": "schema invalid",
        },
        now=now,
    )
    second = enrich_run_state(first, first, now=now + timedelta(seconds=5))

    assert second["retry_attempt_count"] == 1
    assert second["last_failure_at"] == first["last_failure_at"]


def test_same_failure_after_a_real_retry_consumes_next_budget_slot():
    now = datetime(2026, 8, 27, 0, 15, tzinfo=timezone.utc)
    first = enrich_run_state(
        {
            "group_name": "群A",
            "run_date": "2026-08-27",
            "status": FAILED,
            "failed_stage": "data",
            "error_type": MESSAGE_FETCH_FAILED,
            "error": "same timeout",
        },
        now=now,
    )
    retry_started = enrich_run_state(
        {
            **first,
            "status": "PENDING",
            "failure_fingerprint": "",
            "retry_started_at": (now + timedelta(seconds=60)).isoformat(),
        },
        first,
        now=now + timedelta(seconds=60),
    )
    second = enrich_run_state(
        {
            **retry_started,
            "status": FAILED,
            "failed_stage": "data",
            "error_type": MESSAGE_FETCH_FAILED,
            "error": "same timeout",
        },
        retry_started,
        now=now + timedelta(seconds=61),
    )

    assert second["retry_attempt_count"] == 2
    assert len(second["attempt_ledger"]) == 2


def test_unknown_external_result_is_always_manual_hold():
    run = enrich_run_state(
        {
            "group_name": "群A",
            "run_date": "2026-08-27",
            "status": FAILED,
            "failed_stage": "image",
            "error_type": "IMAGE_GENERATION_FAILED",
            "error": "receipt missing",
            "image_job": {"status": "result_unknown"},
        }
    )

    assert run["execution_state"] == EXECUTION_HOLD_MANUAL
    assert run["retryable"] is False
    assert run["next_retry_at"] == ""


def test_retry_budget_exhaustion_becomes_final():
    previous = {
        "retry_attempt_count": 2,
        "retry_budget": 3,
        "failure_fingerprint": "old",
    }
    run = enrich_run_state(
        {
            "group_name": "群A",
            "run_date": "2026-08-27",
            "status": FAILED,
            "failed_stage": "data",
            "error_type": MESSAGE_FETCH_FAILED,
            "error": "third distinct failure",
            "retry_budget": 3,
        },
        previous,
    )

    assert run["retry_attempt_count"] == 3
    assert run["execution_state"] == EXECUTION_FAILED_FINAL


def test_run_store_lazily_enriches_old_run_and_sent_is_complete(tmp_path):
    store = RunStore(tmp_path)
    failed = store.update(
        "群A",
        "2026-08-27",
        status=FAILED,
        failed_stage="data",
        error_type=MESSAGE_FETCH_FAILED,
        error="temporary timeout",
    )
    completed = store.update("群A", "2026-08-27", status=SENT)

    assert failed["execution_state"] == EXECUTION_WAIT_RETRY
    assert failed["last_successful_checkpoint"] == "TASK_CREATED"
    assert completed["execution_state"] == EXECUTION_COMPLETE
    assert completed["last_successful_checkpoint"] == "SENT_CONFIRMED"
    assert completed["next_retry_at"] == ""
