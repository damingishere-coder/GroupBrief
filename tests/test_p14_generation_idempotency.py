"""P1.4 AI/Prompt 幂等测试；所有外部调用均由 Fake 替代。"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.config.settings import Settings
from app.providers.ai.base import (
    ExternalCallNotSubmittedError,
    ExternalCallResultUnknownError,
)
from app.providers.ai.codex import CodexGPTProvider
from app.providers.ai.deepseek import DeepSeekV4FlashProvider
from app.v2.constants import RANKING_READY
from app.v2.recovery import scan_incomplete
from app.v2.run_store import RunStore


def test_recorded_prompt_result_is_recovered_without_second_external_call(tmp_path):
    store = RunStore(tmp_path)
    store.update("测试群", "2026-08-25", status=RANKING_READY)
    operation_id, _, reason = store.claim_prompt_operation(
        "测试群", "2026-08-25", input_hash="input-v1"
    )
    assert reason == "claimed"
    assert operation_id

    store.record_prompt_result(
        "测试群",
        "2026-08-25",
        operation_id,
        prompt="已付费生成的 Prompt",
        meta={"api_call_count": 1},
    )

    recovered_id, recovered, recovered_reason = store.claim_prompt_operation(
        "测试群", "2026-08-25", input_hash="input-v1"
    )
    assert recovered_id is None
    assert recovered_reason == "result_recorded"
    store.commit_recorded_prompt(
        "测试群",
        "2026-08-25",
        recovered["prompt_operation_id"],
    )

    assert store.prompt_path("测试群", "2026-08-25").read_text(encoding="utf-8") == "已付费生成的 Prompt"
    run = store.load_run("测试群", "2026-08-25")
    assert run["prompt_operation_status"] == "succeeded"
    assert run["prompt_operation_result"] is None


def test_unfinished_prompt_operation_becomes_manual_hold_even_with_force(tmp_path):
    store = RunStore(tmp_path)
    store.update("测试群", "2026-08-25", status=RANKING_READY)
    operation_id, _, reason = store.claim_prompt_operation(
        "测试群", "2026-08-25", input_hash="input-v1"
    )
    assert operation_id and reason == "claimed"

    next_id, run, next_reason = store.claim_prompt_operation(
        "测试群", "2026-08-25", input_hash="input-v1", force=True
    )
    assert next_id is None
    assert next_reason == "result_unknown"
    assert run["prompt_hold"] is True
    assert run["prompt_operation_finished_at"] == ""
    assert scan_incomplete(store, "2026-08-25")[0]["recovery_type"] == "manual_review"

    third_id, _, third_reason = store.claim_prompt_operation(
        "测试群", "2026-08-25", input_hash="input-v1", force=True
    )
    assert third_id is None
    assert third_reason == "result_unknown"


def test_prompt_unknown_requires_matching_operation_id_before_retry(tmp_path):
    store = RunStore(tmp_path)
    store.update("测试群", "2026-08-27", status=RANKING_READY)
    operation_id, _, reason = store.claim_prompt_operation(
        "测试群", "2026-08-27", input_hash="input-v1"
    )
    assert operation_id and reason == "claimed"
    store.mark_prompt_result_unknown(
        "测试群",
        "2026-08-27",
        operation_id,
        error="Codex GPT 超时且结果未知",
    )

    resolved, _, stale_reason = store.resolve_prompt_result_unknown(
        "测试群",
        "2026-08-27",
        expected_operation_id="stale-operation",
        now=datetime.now().astimezone(),
    )
    assert resolved is False
    assert stale_reason == "stale"

    resolved, run, resolved_reason = store.resolve_prompt_result_unknown(
        "测试群",
        "2026-08-27",
        expected_operation_id=operation_id,
        now=datetime.now().astimezone(),
    )
    assert resolved is True
    assert resolved_reason == "resolved"
    assert run["prompt_hold"] is False
    assert run["prompt_operation_status"] == "failed"
    assert run["prompt_last_resolution"] == "discard_and_retry"

    next_id, _, next_reason = store.claim_prompt_operation(
        "测试群", "2026-08-27", input_hash="input-v1", force=True
    )
    assert next_reason == "claimed"
    assert next_id and next_id != operation_id


class _Fallback:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def health_check(self):
        return True, "ok"

    def _chat(self, *args, **kwargs):
        self.calls += 1
        return "fallback-result"


def _codex_provider() -> CodexGPTProvider:
    settings = Settings(
        _env_file=None,
        summary_provider_primary="codex",
        summary_provider_fallback="deepseek",
        ai_api_key="test-only",
    )
    provider = CodexGPTProvider(settings)
    provider._fallback = _Fallback()
    return provider


def test_codex_unknown_result_never_calls_fallback(monkeypatch):
    provider = _codex_provider()

    def unknown(*args, **kwargs):
        raise ExternalCallResultUnknownError("result unknown")

    monkeypatch.setattr(provider, "_codex_chat", unknown)
    with pytest.raises(ExternalCallResultUnknownError):
        provider._chat([{"role": "user", "content": "test"}])
    assert provider._fallback.calls == 0


def test_codex_confirmed_not_submitted_can_use_fallback(monkeypatch):
    provider = _codex_provider()

    def not_submitted(*args, **kwargs):
        raise ExternalCallNotSubmittedError("not submitted")

    monkeypatch.setattr(provider, "_codex_chat", not_submitted)
    assert provider._chat([{"role": "user", "content": "test"}]) == "fallback-result"
    assert provider._fallback.calls == 1


def _deepseek_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "summary_provider_primary": "deepseek",
        "ai_api_key": "test-only",
        "ai_max_retries": 3,
    }
    values.update(overrides)
    return Settings(**values)


def test_deepseek_read_timeout_is_unknown_and_not_retried(monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(1)
        request = httpx.Request("POST", "https://api.example.test/chat/completions")
        raise httpx.ReadTimeout("timeout", request=request)

    monkeypatch.setattr(httpx, "post", timeout)
    provider = DeepSeekV4FlashProvider(_deepseek_settings())
    with pytest.raises(ExternalCallResultUnknownError):
        provider._chat([{"role": "user", "content": "test"}])
    assert len(calls) == 1


def test_deepseek_explicit_429_can_retry(monkeypatch):
    calls = []
    responses = [
        httpx.Response(429),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        ),
    ]

    def respond(*args, **kwargs):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr(httpx, "post", respond)
    monkeypatch.setattr("app.providers.ai.deepseek.time.sleep", lambda _seconds: None)
    provider = DeepSeekV4FlashProvider(_deepseek_settings())
    assert provider._chat([{"role": "user", "content": "test"}]) == "ok"
    assert len(calls) == 2


def test_deepseek_500_is_unknown_and_not_retried(monkeypatch):
    calls = []

    def respond(*args, **kwargs):
        calls.append(1)
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "post", respond)
    provider = DeepSeekV4FlashProvider(_deepseek_settings())
    with pytest.raises(ExternalCallResultUnknownError):
        provider._chat([{"role": "user", "content": "test"}])
    assert len(calls) == 1
