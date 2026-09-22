from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from app.config.settings import Settings
from app.db.models import Group
from app.services.group_defaults import inherited_group_workflow
from app.services.group_provider_config import resolve_group_ai_settings
from app.providers.ai.codex import CodexGPTProvider, validate_summary_provider_config
from app.image.codex_generator import CodexImageGenerator


def test_split_routes_and_new_group_inherit_without_fallback():
    settings = Settings(_env_file=None)
    group = Group(id=1, display_name="test", wechat_group_id="test", enabled=True,
                  summary_provider="deepseek", summary_model="deepseek-flash",
                  prompt_provider="codex", prompt_model="gpt-5.6-luna")
    inherited = Group(display_name="new", **inherited_group_workflow([group]))
    for selected in (group, inherited):
        summary, _ = resolve_group_ai_settings(settings, selected, capability="summary")
        prompt, _ = resolve_group_ai_settings(settings, selected, capability="prompt")
        assert summary.summary_provider_primary == "deepseek"
        assert summary.ai_model == "deepseek-flash"
        assert summary.summary_provider_fallback == ""
        assert prompt.codex_summary_model == "gpt-5.6-luna"
        assert prompt.codex_reasoning_effort == "max"
        assert prompt.summary_provider_fallback == "none"


def test_luna_text_command_and_usage(monkeypatch):
    provider = CodexGPTProvider(Settings(_env_file=None))
    provider._resolved_binary = "codex"
    def run(command, **kwargs):
        assert command[command.index("--model") + 1] == "gpt-5.6-luna"
        assert 'model_reasoning_effort="max"' in command
        assert "--json" in command
        Path(command[command.index("--output-last-message") + 1]).write_text("ok", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"type":"turn.completed", "usage":{"input_tokens":10,"output_tokens":2}}), stderr="")
    monkeypatch.setattr("app.providers.ai.codex.subprocess.run", run)
    assert provider._chat([{"role":"user", "content":"test"}]) == "ok"
    assert provider.usage_records[0]["usage"]["input_tokens"] == 10
    assert provider._fallback is None


def test_image_executor_luna_max_and_single_attempt(tmp_path):
    generator = CodexImageGenerator(Settings(_env_file=None), max_attempts=1)
    generator._resolved_binary = "codex"
    command = generator._build_command(tmp_path / "result.json")
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="max"' in command
    assert generator.max_attempts == 1


def test_runtime_invalid_reasoning_is_rejected():
    settings = Settings(_env_file=None)
    settings.apply_runtime_values({"codex_reasoning_effort":"invalid"})
    with pytest.raises(ValueError, match="思考强度"):
        validate_summary_provider_config(settings)


def test_parallel_groups_do_not_share_usage_or_stale_settings():
    from app.pipeline.daily_pipeline import DailyPipeline
    pipeline = object.__new__(DailyPipeline)
    pipeline._prompt_builder_injected = False
    settings = Settings(_env_file=None)
    first = pipeline._prompt_builder_for_group(settings, settings)
    second = pipeline._prompt_builder_for_group(settings, settings)
    first._prompt_provider.usage_records.append({"usage":{"input_tokens":10}})
    assert second._prompt_provider.usage_records == []


def test_old_explicit_model_remains_readable_after_default_switch():
    settings = Settings(_env_file=None, summary_provider_primary="deepseek")
    group = Group(display_name="old", prompt_provider="", prompt_model="gpt-6-astra")
    selected, _ = resolve_group_ai_settings(settings, group, capability="prompt")
    assert selected.summary_provider_primary == "codex"
    assert selected.codex_summary_model == "gpt-6-astra"


def test_comparison_refuses_source_or_existing_directory(tmp_path):
    from scripts.compare_model_snapshot import compare
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError):
        compare(source, source / "nested")
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(FileExistsError):
        compare(source, target)
