from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config.settings import Settings
from app.providers.ai.base import ExternalCallNotSubmittedError
from app.providers.ai.codex import CodexGPTProvider, build_summary_provider


class _Fallback:
    def __init__(self, *, configured: bool = True, result: str = "deepseek-ok", error: Exception | None = None):
        self.configured = configured
        self.result = result
        self.error = error
        self.calls = 0

    def health_check(self):
        return self.configured, "configured" if self.configured else "missing"

    def _chat(self, messages, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "summary_provider_primary": "codex",
        "summary_provider_fallback": "deepseek",
        "codex_summary_model": "gpt-5.6-sol",
        "codex_summary_max_retries": 1,
        "codex_summary_timeout_seconds": 30,
        "codex_summary_request_concurrency": 1,
        "ai_api_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_codex_success_uses_stdin_read_only_and_does_not_call_fallback(monkeypatch):
    provider = CodexGPTProvider(_settings())
    provider._resolved_binary = "codex.CMD"
    fallback = _Fallback()
    provider._fallback = fallback
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        captured["timeout"] = kwargs["timeout"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"events": []}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.providers.ai.codex.subprocess.run", fake_run)
    result = provider._chat(
        [{"role": "user", "content": "私密群聊正文"}],
        response_format="json_object",
    )

    assert result == '{"events": []}'
    assert fallback.calls == 0
    assert "私密群聊正文" not in " ".join(captured["command"])
    assert "私密群聊正文" in captured["input"]
    assert captured["command"][captured["command"].index("--model") + 1] == "gpt-5.6-sol"
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in captured["command"]
    assert "--ignore-user-config" in captured["command"]
    assert "--ignore-rules" in captured["command"]
    assert 'model_reasoning_effort="medium"' in captured["command"]
    assert captured["timeout"] == 30


def test_default_codex_summary_timeout_allows_long_structured_responses():
    assert Settings(_env_file=None).codex_summary_timeout_seconds == 600


def test_codex_confirmed_not_submitted_uses_deepseek_fallback(monkeypatch):
    provider = CodexGPTProvider(_settings(ai_api_key="fake"))
    fallback = _Fallback(result="备用成功")
    provider._fallback = fallback
    monkeypatch.setattr(
        provider,
        "_codex_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExternalCallNotSubmittedError("主模型未提交")
        ),
    )

    assert provider._chat([{"role": "user", "content": "test"}]) == "备用成功"
    assert fallback.calls == 1


def test_codex_not_submitted_and_deepseek_failure_returns_clear_error(monkeypatch):
    provider = CodexGPTProvider(_settings(ai_api_key="fake"))
    provider._fallback = _Fallback(error=RuntimeError("备用失败"))
    monkeypatch.setattr(
        provider,
        "_codex_chat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExternalCallNotSubmittedError("主模型未提交")
        ),
    )

    with pytest.raises(RuntimeError, match="未提交且 DeepSeek 备用失败"):
        provider._chat([{"role": "user", "content": "test"}])


def test_default_factory_builds_codex_gpt_provider():
    provider = build_summary_provider(_settings())
    assert isinstance(provider, CodexGPTProvider)
    assert provider.model == "gpt-5.6-sol"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"summary_provider_primary": "unknown-ai"}, "主 Provider"),
        ({"summary_provider_fallback": "unknown-ai"}, "备用 Provider"),
    ],
)
def test_summary_factory_rejects_unknown_provider_names(overrides, message):
    with pytest.raises(ValueError, match=message):
        build_summary_provider(_settings(**overrides))
