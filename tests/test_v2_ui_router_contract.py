from fastapi import FastAPI

from app.api import v2_ui


EXPECTED_V2_UI_OPERATIONS = {
    ("GET", "/api/v2/dashboard", "dashboard_api_v2_dashboard_get"),
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
    ("POST", "/api/v2/runs/{group}/{run_date}/refresh-messages", "refresh_run_messages_api_v2_runs__group___run_date__refresh_messages_post"),
    ("POST", "/api/v2/runs/{group}/{run_date}/rebuild-prompt", "rebuild_run_prompt_api_v2_runs__group___run_date__rebuild_prompt_post"),
    ("GET", "/api/v2/system/health", "system_health_api_v2_system_health_get"),
    ("GET", "/api/v2/system/startup", "startup_checks_api_v2_system_startup_get"),
    ("GET", "/api/v2/system/recovery", "recovery_info_api_v2_system_recovery_get"),
    ("POST", "/api/v2/pipeline/retry-failed", "retry_failed_api_v2_pipeline_retry_failed_post"),
    ("POST", "/api/v2/pipeline/generate", "pipeline_generate_api_v2_pipeline_generate_post"),
    ("POST", "/api/v2/pipeline/send-due", "pipeline_send_due_api_v2_pipeline_send_due_post"),
    ("POST", "/api/v2/pipeline/send", "pipeline_send_api_v2_pipeline_send_post"),
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
