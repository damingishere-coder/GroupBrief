from types import SimpleNamespace

from app.api.v2_ui_read import list_runs
from app.v2.run_store import RunStore


def test_list_runs_can_include_allowed_files_without_reloading_each_detail(tmp_path):
    store = RunStore(tmp_path)
    store.save_run(
        "测试群",
        "2026-08-25",
        {
            "group_name": "测试群",
            "run_date": "2026-08-25",
            "status": "PROMPT_READY",
        },
    )
    store.messages_path("测试群", "2026-08-25").write_text("[]", encoding="utf-8")
    (store.group_dir("测试群", "2026-08-25") / "private.tmp").write_text(
        "not public",
        encoding="utf-8",
    )

    response = list_runs(
        settings=SimpleNamespace(output_dir=tmp_path),
        include_files=True,
    )

    assert response["total"] == 1
    assert response["runs"][0]["files"] == ["messages.json", "run.json"]


def test_list_runs_default_response_does_not_add_file_scan_field(tmp_path):
    store = RunStore(tmp_path)
    store.save_run(
        "测试群",
        "2026-08-25",
        {
            "group_name": "测试群",
            "run_date": "2026-08-25",
            "status": "PENDING",
        },
    )

    response = list_runs(settings=SimpleNamespace(output_dir=tmp_path))

    assert "files" not in response["runs"][0]
