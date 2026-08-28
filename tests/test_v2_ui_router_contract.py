from pathlib import Path
import json
from types import SimpleNamespace

from fastapi import FastAPI

from app.api import v2_ui, v2_ui_read
from app.config.settings import Settings


EXPECTED_V2_UI_OPERATIONS = {
    ("GET", "/api/v2/dashboard", "dashboard_api_v2_dashboard_get"),
    ("GET", "/api/v2/runtime/logs", "runtime_logs_api_v2_runtime_logs_get"),
    ("GET", "/api/v2/runs", "list_runs_api_v2_runs_get"),
    ("GET", "/api/v2/archive/groups", "archive_groups_api_v2_archive_groups_get"),
    ("GET", "/api/v2/runs/{group}/{run_date}", "run_detail_api_v2_runs__group___run_date__get"),
    ("GET", "/api/v2/files/{group}/{run_date}/{file_name}", "read_output_file_api_v2_files__group___run_date___file_name__get"),
    ("GET", "/api/v2/image-themes", "image_themes_api_v2_image_themes_get"),
    ("POST", "/api/v2/image-themes/resolve", "resolve_theme_preview_api_v2_image_themes_resolve_post"),
    ("GET", "/api/v2/runs/{group}/{run_date}/prompt", "get_run_prompt_api_v2_runs__group___run_date__prompt_get"),
    ("PUT", "/api/v2/runs/{group}/{run_date}/prompt", "update_run_prompt_api_v2_runs__group___run_date__prompt_put"),
    ("POST", "/api/v2/runs/{group}/{run_date}/prompt/restore", "restore_run_prompt_api_v2_runs__group___run_date__prompt_restore_post"),
    ("POST", "/api/v2/runs/{group}/{run_date}/regenerate-image", "regenerate_run_image_api_v2_runs__group___run_date__regenerate_image_post"),
    ("GET", "/api/v2/runs/{group}/{run_date}/image-candidates", "get_run_image_candidates_api_v2_runs__group___run_date__image_candidates_get"),
    ("GET", "/api/v2/runs/{group}/{run_date}/image-candidates/{candidate_id}", "preview_run_image_candidate_api_v2_runs__group___run_date__image_candidates__candidate_id__get"),
    ("POST", "/api/v2/runs/{group}/{run_date}/image-candidates/claim", "claim_run_image_candidate_api_v2_runs__group___run_date__image_candidates_claim_post"),
    ("POST", "/api/v2/runs/{group}/{run_date}/refresh-messages", "refresh_run_messages_api_v2_runs__group___run_date__refresh_messages_post"),
    ("POST", "/api/v2/runs/{group}/{run_date}/rebuild-prompt", "rebuild_run_prompt_api_v2_runs__group___run_date__rebuild_prompt_post"),
    ("POST", "/api/v2/runs/batch/rebuild-prompts", "rebuild_run_prompts_batch_api_v2_runs_batch_rebuild_prompts_post"),
    ("POST", "/api/v2/runs/batch/regenerate-images", "regenerate_run_images_batch_api_v2_runs_batch_regenerate_images_post"),
    ("GET", "/api/v2/system/health", "system_health_api_v2_system_health_get"),
    ("GET", "/api/v2/system/startup", "startup_checks_api_v2_system_startup_get"),
    ("GET", "/api/v2/system/recovery", "recovery_info_api_v2_system_recovery_get"),
    ("POST", "/api/v2/pipeline/retry-failed", "retry_failed_api_v2_pipeline_retry_failed_post"),
    ("POST", "/api/v2/pipeline/generate", "pipeline_generate_api_v2_pipeline_generate_post"),
    ("POST", "/api/v2/pipeline/send-due", "pipeline_send_due_api_v2_pipeline_send_due_post"),
    ("POST", "/api/v2/pipeline/send", "pipeline_send_api_v2_pipeline_send_post"),
    ("POST", "/api/v2/pipeline/resolve-send-unknown", "pipeline_resolve_send_unknown_api_v2_pipeline_resolve_send_unknown_post"),
    ("POST", "/api/v2/pipeline/resolve-prompt-unknown", "pipeline_resolve_prompt_unknown_api_v2_pipeline_resolve_prompt_unknown_post"),
    ("POST", "/api/v2/pipeline/resolve-manual-send", "pipeline_resolve_manual_send_api_v2_pipeline_resolve_manual_send_post"),
    ("GET", "/api/v2/recovery/backlog", "recovery_backlog_api_v2_recovery_backlog_get"),
    ("POST", "/api/v2/recovery/confirm", "confirm_recovery_api_v2_recovery_confirm_post"),
    ("POST", "/api/v2/recovery/repair-empty-manifest", "repair_empty_manifest_api_v2_recovery_repair_empty_manifest_post"),
    ("GET", "/api/v2/weekly", "list_weekly_insights_api_v2_weekly_get"),
    ("GET", "/api/v2/weekly/{week_start}/{group_id}", "weekly_insight_detail_api_v2_weekly__week_start___group_id__get"),
    ("GET", "/api/v2/weekly/{week_start}/{group_id}/card", "weekly_insight_card_api_v2_weekly__week_start___group_id__card_get"),
}


def test_v2_ui_router_preserves_paths_methods_and_operation_ids() -> None:
    app = FastAPI()
    app.include_router(v2_ui.router)
    schema = app.openapi()
    actual = {
        (method.upper(), path, operation["operationId"])
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method != "parameters"
    }

    assert actual == EXPECTED_V2_UI_OPERATIONS


def test_v2_ui_compatibility_exports_remain_available() -> None:
    assert v2_ui.router is not None
    assert v2_ui.RunPromptUpdateBody is not None
    assert callable(v2_ui.update_run_prompt)
    assert callable(v2_ui.restore_run_prompt)
    assert v2_ui.RetryBody is not None
    assert callable(v2_ui.retry_failed)
    assert callable(v2_ui._store)


def test_dashboard_counts_send_unknown_as_held_and_surfaces_send_error(
    tmp_path, monkeypatch
) -> None:
    group = SimpleNamespace(
        id=7,
        display_name="测试群",
        wechat_group_name="测试群",
        send_time="08:30",
        schedule_rule="daily",
        image_enabled=True,
        wechat_send_enabled=True,
        ranking_template="",
        image_prompt_template="",
    )

    class FakeStore:
        def load_run(self, _group_name, _run_date):
            return {
                "status": "READY_TO_SEND",
                "send_state": "unknown",
                "send_hold": True,
                "send_hold_reason": "SEND_RESULT_UNKNOWN",
                "send_error": "文字已提交但 UI 验证结果未知",
                "send_error_type": "SEND_RESULT_UNKNOWN",
                "send_unknown_at": "2026-08-26T08:30:59+08:00",
            }

        def image_path(self, _group_name, _run_date):
            return Path(tmp_path) / "missing.png"

        def ranking_json_path(self, _group_name, _run_date):
            return Path(tmp_path) / "missing-ranking.json"

    monkeypatch.setattr(v2_ui_read, "_store", lambda _settings: FakeStore())
    monkeypatch.setattr(
        v2_ui_read.repo,
        "list_groups",
        lambda _session, only_enabled=True: [group],
    )

    result = v2_ui_read.dashboard(
        session=object(),
        settings=Settings(_env_file=None, output_dir=tmp_path),
    )

    assert result["counts"] == {
        "pending": 0,
        "generated": 0,
        "sent": 0,
        "failed": 0,
        "held": 1,
    }
    assert result["cards"][0]["error"] == "文字已提交但 UI 验证结果未知"
    assert result["cards"][0]["send_error_type"] == "SEND_RESULT_UNKNOWN"


def test_dashboard_counts_prompt_unknown_as_held(tmp_path, monkeypatch) -> None:
    group = SimpleNamespace(
        id=9,
        display_name="Prompt 暂停群",
        wechat_group_name="Prompt 暂停群",
        send_time="08:30",
        schedule_rule="daily",
        image_enabled=True,
        wechat_send_enabled=True,
        ranking_template="",
        image_prompt_template="",
    )

    class FakeStore:
        def load_run(self, _group_name, _run_date):
            return {
                "status": "RANKING_READY",
                "prompt_hold": True,
                "prompt_hold_reason": "PROMPT_RESULT_UNKNOWN",
                "prompt_operation_id": "operation-123",
                "prompt_operation_status": "unknown",
                "error": "Codex GPT 超时且结果未知",
            }

        def image_path(self, _group_name, _run_date):
            return Path(tmp_path) / "missing.png"

        def ranking_json_path(self, _group_name, _run_date):
            return Path(tmp_path) / "missing-ranking.json"

    monkeypatch.setattr(v2_ui_read, "_store", lambda _settings: FakeStore())
    monkeypatch.setattr(v2_ui_read.repo, "list_groups", lambda *_args, **_kwargs: [group])

    result = v2_ui_read.dashboard(
        session=object(),
        settings=Settings(_env_file=None, output_dir=tmp_path),
        run_date="2026-08-27",
    )

    assert result["counts"]["held"] == 1
    assert result["counts"]["pending"] == 0
    assert result["cards"][0]["prompt_operation_id"] == "operation-123"


def test_dashboard_accepts_run_date_and_returns_top_five_ranking_preview(
    tmp_path, monkeypatch
) -> None:
    group = SimpleNamespace(
        id=8,
        display_name="排行测试群",
        wechat_group_name="排行测试群",
        send_time="08:30",
        schedule_rule="daily",
        image_enabled=False,
        wechat_send_enabled=False,
        ranking_template="",
        image_prompt_template="",
    )
    ranking_path = Path(tmp_path) / "ranking.json"
    ranking_path.write_text(
        json.dumps(
            {
                "top_speakers": [
                    {"rank": index, "name": f"成员{index}", "count": 20 - index}
                    for index in range(1, 8)
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeStore:
        def load_run(self, _group_name, run_date):
            assert run_date == "2026-08-25"
            return {
                "status": "RANKING_READY",
                "period_start": "2026-08-25 00:00:00",
                "period_end": "2026-08-25 23:59:59",
                "message_count": 99,
                "speaker_count": 7,
            }

        def image_path(self, _group_name, _run_date):
            return Path(tmp_path) / "missing.png"

        def ranking_json_path(self, _group_name, _run_date):
            return ranking_path

    monkeypatch.setattr(v2_ui_read, "_store", lambda _settings: FakeStore())
    monkeypatch.setattr(v2_ui_read.repo, "list_groups", lambda *_args, **_kwargs: [group])

    result = v2_ui_read.dashboard(
        session=object(),
        settings=Settings(_env_file=None, output_dir=tmp_path),
        run_date="2026-08-25",
    )

    assert result["today"] == result["run_date"] == "2026-08-25"
    assert result["runtime"]["overall_status"] == "needs_attention"
    assert [node["id"] for node in result["runtime"]["nodes"]] == [
        "scheduler",
        "data",
        "ranking",
        "prompt",
        "image",
        "send",
    ]
    assert result["runtime"]["groups"][0]["current_node"] == "prompt"
    assert result["daily_status"]["overall_status"] == result["runtime"]["overall_status"]
    assert len(result["cards"][0]["ranking_preview"]) == 5
    assert result["cards"][0]["ranking_preview"][0] == {
        "rank": 1,
        "name": "成员1",
        "count": 19,
        "text_count": 0,
        "interaction_count": 0,
        "name_source": "resolved",
    }
