import json
from datetime import datetime
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.data_sources.base import V2Message
from app.providers.ai.base import ExternalCallResultUnknownError
from scripts import compare_model_snapshot as comparison


@pytest.mark.parametrize("outcome", ["complete", "prompt_failed", "image_failed", "unknown"])
def test_comparison_isolated_with_no_image_retry(tmp_path, monkeypatch, outcome):
    source = tmp_path / "source"
    source.mkdir()
    run = {"group_id":"1", "group_name":"test", "run_date":"2026-09-22", "report_kind":"daily",
           "period_start":"2026-09-21 00:00:00", "period_end":"2026-09-21 23:59:59", "status":"SENT"}
    for name, value in {"run.json":run, "ranking.json":{"message_count":1,"speaker_count":1},"messages.json":[]}.items():
        (source / name).write_text(json.dumps(value), encoding="utf-8")
    for name in ("ranking.txt", "image_prompt.txt", "daily_image.png"):
        (source / name).write_bytes(b"original")
    hashes = {p.name:comparison.digest(p) for p in source.iterdir()}
    database = tmp_path / "test.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE groups(id INTEGER,image_prompt_override TEXT); INSERT INTO groups VALUES(1,'');")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database.as_posix()}", ai_api_key="test-only-key")
    monkeypatch.setattr(comparison, "Settings", lambda:settings)
    message = V2Message("m1","1","test","s1","member",datetime(2026,9,21,12),content="hello")
    monkeypatch.setattr(comparison.DailyPipeline, "_load_message_snapshot", lambda path:[message])
    monkeypatch.setattr(comparison, "RunStore", lambda path:SimpleNamespace(previous_theme_signature=lambda *a:"", recent_layout_history=lambda *a,**k:()))
    calls = []
    class Builder:
        def __init__(self, prompt_settings, *, summary_settings):
            assert prompt_settings.codex_summary_model == "gpt-5.6-luna"
            assert prompt_settings.codex_reasoning_effort == "max"
            assert summary_settings.summary_provider_primary == "deepseek"
        def build(self, data):
            assert data.persisted_topic_selection is None
            assert data.persisted_theme_meta is None
            calls.append("prompt")
            if outcome == "unknown":
                raise ExternalCallResultUnknownError("test")
            return SimpleNamespace(success=outcome!="prompt_failed", prompt="new prompt", error="failed", meta={})
    class Generator:
        def __init__(self, selected_settings, *, max_attempts):
            assert selected_settings.codex_image_model == "gpt-5.6-luna"
            assert max_attempts == 1
        def generate(self, prompt_path, image_path):
            calls.append("image")
            image_path.write_bytes(b"new-image")
            return SimpleNamespace(success=outcome!="image_failed", image_path=image_path, error="", detail={})
    monkeypatch.setattr(comparison, "DeepSeekImagePromptBuilder", Builder)
    monkeypatch.setattr(comparison, "CodexImageGenerator", Generator)
    monkeypatch.setattr(comparison, "strip_unverified_prompt_numeric_units", lambda path:(path.read_text(encoding="utf-8"),[]))
    monkeypatch.setattr(comparison, "verify_image_contract", lambda *a:(True,"checked"))
    destination = tmp_path / "comparison"
    if outcome == "unknown":
        with pytest.raises(ExternalCallResultUnknownError):
            comparison.compare(source, destination)
    else:
        result = comparison.compare(source, destination)
        assert result["status"] == outcome
    report = json.loads((destination/"comparison.json").read_text(encoding="utf-8"))
    assert report["source_unchanged"] is True
    assert report["send_enabled"] is False
    assert {p.name:comparison.digest(p) for p in source.iterdir()} == hashes
    assert calls == (["prompt"] if outcome in {"prompt_failed","unknown"} else ["prompt","image"])
    with pytest.raises(FileExistsError):
        comparison.compare(source, destination)
