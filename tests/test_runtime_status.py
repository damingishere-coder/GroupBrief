import json

from app.scheduler.daily_v2_job import DailyScheduleState
from app.scheduler.runtime_status import build_daily_status, write_daily_status
from app.v2.constants import READY_TO_SEND, SENT
from app.v2.run_store import RunStore


def test_runtime_status_summarizes_groups_without_business_payload(tmp_path):
    store = RunStore(tmp_path / "output")
    DailyScheduleState(store.root).update(
        "2026-08-27",
        manifest_version=1,
        manifest_created_at="2026-08-27T00:14:00+08:00",
        expected_group_count=2,
        expected_groups=[
            {"group_id": 1, "expected_terminal": SENT},
            {"group_id": 2, "expected_terminal": SENT},
        ],
        generation_started_at="2026-08-27T00:15:00+08:00",
        generation_status="success",
        generation_completed_at="2026-08-27T00:16:00+08:00",
    )
    store.save_run(
        "群A",
        "2026-08-27",
        {
            "group_id": "1",
            "status": SENT,
            "sent_at": "2026-08-27T08:45:00+08:00",
            "prompt_meta": {"api_model": "model-a"},
            "messages": "不得写入报告",
        },
    )
    store.save_run("群B", "2026-08-27", {"group_id": "2", "status": READY_TO_SEND})

    path = write_daily_status(store, "2026-08-27")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path == tmp_path / "runtime" / "2026-08-27" / "status.json"
    assert payload["run_id"].startswith("groupbrief:2026-08-27:")
    assert payload["overall_status"] == "partial"
    assert payload["summary"]["expected_group_count"] == 2
    assert payload["summary"]["completed_group_count"] == 1
    assert [item["group_task_id"] for item in payload["groups"]] == [
        "groupbrief:2026-08-27:group-1",
        "groupbrief:2026-08-27:group-2",
    ]
    assert payload["groups"][0]["send"]["status"] == "success"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "不得写入报告" not in serialized
    assert not list(path.parent.glob("*.tmp"))


def test_runtime_status_exposes_send_retry_and_final_hold(tmp_path):
    store = RunStore(tmp_path / "output")
    DailyScheduleState(store.root).update(
        "2026-08-27",
        manifest_version=1,
        manifest_created_at="2026-08-27T00:14:00+08:00",
        expected_group_count=2,
        expected_groups=[
            {"group_id": 3, "expected_terminal": SENT},
            {"group_id": 4, "expected_terminal": SENT},
        ],
    )
    store.save_run(
        "重试群",
        "2026-08-27",
        {
            "group_id": "3",
            "status": READY_TO_SEND,
            "send_state": "ready",
            "send_retry_attempt_count": 1,
            "send_retry_budget": 3,
            "send_next_retry_at": "2026-08-27T08:46:00+08:00",
        },
    )
    store.save_run(
        "终止群",
        "2026-08-27",
        {
            "group_id": "4",
            "status": READY_TO_SEND,
            "send_state": "failed_final",
            "send_retry_attempt_count": 3,
            "send_retry_budget": 3,
            "send_hold": True,
            "send_hold_reason": "SEND_RETRY_EXHAUSTED",
        },
    )

    payload = json.loads(write_daily_status(store, "2026-08-27").read_text(encoding="utf-8"))
    by_name = {row["group_name"]: row for row in payload["groups"]}

    assert by_name["重试群"]["send"]["status"] == "retry_pending"
    assert by_name["重试群"]["send"]["attempts"] == 1
    assert by_name["终止群"]["send"]["status"] == "held"
    assert by_name["终止群"]["send"]["hold_reason"] == "SEND_RETRY_EXHAUSTED"
    assert payload["overall_status"] == "blocked"


def test_runtime_status_never_completes_when_manifest_group_is_missing(tmp_path):
    store = RunStore(tmp_path / "output")
    DailyScheduleState(store.root).update(
        "2026-08-27",
        manifest_version=1,
        manifest_created_at="2026-08-27T00:14:00+08:00",
        expected_group_count=2,
        expected_groups=[
            {"group_id": 1, "expected_terminal": SENT},
            {"group_id": 2, "expected_terminal": SENT},
        ],
    )
    store.save_run(
        "仅完成群",
        "2026-08-27",
        {"group_id": "1", "status": SENT, "sent_at": "2026-08-27T08:30:00+08:00"},
    )

    payload = json.loads(write_daily_status(store, "2026-08-27").read_text(encoding="utf-8"))

    assert payload["overall_status"] == "needs_attention"
    assert payload["summary"]["missing_expected_group_ids"] == ["2"]
    assert payload["summary"]["manifest_complete"] is False


def test_build_daily_status_projects_live_nodes_without_writing_runtime_file(tmp_path):
    store = RunStore(tmp_path / "output")
    DailyScheduleState(store.root).update(
        "2026-08-27",
        manifest_version=1,
        manifest_created_at="2026-08-27T00:14:00+08:00",
        expected_group_count=2,
        expected_groups=[
            {"group_id": 1, "expected_terminal": SENT},
            {"group_id": 2, "expected_terminal": SENT},
        ],
        generation_started_at="2026-08-27T00:15:00+08:00",
        generation_status="running",
    )
    store.save_run("群A", "2026-08-27", {"group_id": "1", "status": "DATA_READY"})
    runs = [
        store.load_run("群A", "2026-08-27"),
        {"group_id": "2", "group_name": "群B", "run_date": "2026-08-27", "status": "PENDING"},
    ]

    payload = build_daily_status(store, "2026-08-27", runs=runs)
    by_node = {item["id"]: item for item in payload["nodes"]}
    by_group = {item["group_name"]: item for item in payload["groups"]}

    assert payload["overall_status"] == "running"
    assert payload["scheduler"]["scheduled_at"].startswith("2026-08-27T00:15:00")
    assert by_node["scheduler"]["status"] == "success"
    assert by_node["ranking"]["status"] == "running"
    assert by_group["群A"]["current_node"] == "ranking"
    assert by_group["群A"]["node_status"] == "running"
    assert by_group["群B"]["node_status"] == "pending"
    assert not (tmp_path / "runtime" / "2026-08-27" / "status.json").exists()


def test_build_daily_status_marks_unknown_send_and_corrupt_scheduler_fail_closed(tmp_path):
    store = RunStore(tmp_path / "output")
    scheduler_path = store.root / ".scheduler" / "2026-08-27.json"
    scheduler_path.parent.mkdir(parents=True)
    scheduler_path.write_text("not-json", encoding="utf-8")
    store.save_run(
        "暂停群",
        "2026-08-27",
        {
            "group_id": "1",
            "status": READY_TO_SEND,
            "send_state": "unknown",
            "send_hold": True,
            "send_hold_reason": "SEND_RESULT_UNKNOWN",
        },
    )

    payload = build_daily_status(store, "2026-08-27")

    assert payload["overall_status"] == "needs_attention"
    assert payload["scheduler"]["state_status"] == "corrupt"
    assert payload["nodes"][0]["status"] == "held"
    assert payload["groups"][0]["send"]["status"] == "held"
    assert payload["groups"][0]["last_error_summary"] == "SEND_RESULT_UNKNOWN"


def test_build_daily_status_keeps_queued_image_pending_instead_of_claiming_running(tmp_path):
    store = RunStore(tmp_path / "output")
    DailyScheduleState(store.root).update(
        "2026-08-27",
        manifest_version=1,
        manifest_created_at="2026-08-27T00:14:00+08:00",
        expected_group_count=1,
        expected_groups=[{"group_id": 1, "expected_terminal": SENT}],
        generation_started_at="2026-08-27T00:15:00+08:00",
        generation_status="running",
    )
    store.save_run(
        "排队群",
        "2026-08-27",
        {
            "group_id": "1",
            "status": "PROMPT_READY",
            "image_job": {"status": "queued"},
        },
    )

    payload = build_daily_status(store, "2026-08-27")
    group = payload["groups"][0]

    assert group["current_node"] == "image"
    assert group["image"]["job_status"] == "queued"
    assert group["image"]["status"] == "pending"
    assert group["node_status"] == "pending"
