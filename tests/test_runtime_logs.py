from fastapi import HTTPException
import pytest

from app.api.v2_ui_read import runtime_logs
from app.config.settings import Settings
from app.services.runtime_logs import read_runtime_logs


def test_runtime_logs_filters_sorts_redacts_and_truncates(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "scheduler.log").write_text(
        "2026-08-27 00:15:03,001 [INFO] groupbrief.scheduler: 调度启动 token=secret-token\n"
        "2026-08-27 00:15:05,001 [ERROR] groupbrief.scheduler: Prompt=完整敏感正文\n"
        "Traceback: password=hunter2\n"
        "2026-08-28 00:15:00,001 [INFO] groupbrief.scheduler: 另一天\n",
        encoding="utf-8",
    )
    (logs_dir / "provider.log").write_text(
        "2026-08-27 00:15:04,001 [WARNING] groupbrief.providers: 数据源响应较慢\n",
        encoding="utf-8",
    )

    result = read_runtime_logs(logs_dir, "2026-08-27", tail=2)

    assert [(item["source"], item["level"]) for item in result["items"]] == [
        ("provider", "WARNING"),
        ("scheduler", "ERROR"),
    ]
    assert result["truncated"] is True
    serialized = str(result)
    assert "secret-token" not in serialized
    assert "完整敏感正文" not in serialized
    assert "hunter2" not in serialized
    assert "另一天" not in serialized


def test_runtime_logs_honors_source_and_level_filters(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "ai.log").write_text(
        "2026-08-27 00:15:03,001 [INFO] groupbrief.ai: 开始摘要\n"
        "2026-08-27 00:15:04,001 [ERROR] groupbrief.ai: 摘要失败\n",
        encoding="utf-8",
    )

    result = read_runtime_logs(
        logs_dir,
        "2026-08-27",
        sources="ai",
        levels="error",
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["message"] == "摘要失败"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"tail": 0}, "tail"),
        ({"tail": 201}, "tail"),
        ({"sources": "../../.env"}, "不支持"),
        ({"levels": "TRACE"}, "不支持"),
    ],
)
def test_runtime_logs_rejects_out_of_contract_filters(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        read_runtime_logs(tmp_path, "2026-08-27", **kwargs)


def test_runtime_logs_api_returns_422_for_invalid_filter(tmp_path):
    settings = Settings(
        _env_file=None,
        logs_dir=tmp_path / "logs",
        output_dir=tmp_path / "output",
    )

    with pytest.raises(HTTPException) as exc_info:
        runtime_logs(
            run_date="2026-08-27",
            tail=100,
            sources="secrets",
            levels=None,
            settings=settings,
        )

    assert exc_info.value.status_code == 422
