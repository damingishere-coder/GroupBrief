"""自然分段、事件证据校验与重试策略。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from app.ai.conversation_segments import PromptMessage, segment_messages
from app.ai.deepseek_events import deduplicate_event_cards, parse_event_cards
from app.config.settings import Settings
from app.providers.ai.deepseek import DeepSeekV4FlashProvider


def _message(index: int, *, at: datetime, size: int = 80) -> PromptMessage:
    return PromptMessage(f"m-{index}", at, f"成员{index}", f"消息{index}-" + ("内容" * size))


def test_under_direct_limit_is_one_complete_submission():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [_message(index, at=start + timedelta(minutes=index), size=5) for index in range(70)]
    chunks = segment_messages(messages, direct_chars=50_000)
    assert len(chunks) == 1
    assert chunks[0].message_ids[0] == "m-0"
    assert chunks[0].message_ids[-1] == "m-69"
    assert "消息69" in chunks[0].text


def test_time_gap_is_preferred_and_disordered_input_is_sorted():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [
        *[_message(index, at=start + timedelta(minutes=index), size=25) for index in range(8)],
        *[_message(index + 8, at=start + timedelta(minutes=40 + index), size=25) for index in range(8)],
    ]
    messages.reverse()
    chunks = segment_messages(messages, direct_chars=1_000, target_chars=1_000, hard_chars=50_000)
    assert len(chunks) == 2
    assert chunks[0].message_ids[0] == "m-0"
    assert chunks[1].message_ids[0] == "m-8"
    assert not (set(chunks[0].message_ids) & set(chunks[1].message_ids))


def test_continuous_chat_uses_eight_message_overlap():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [_message(index, at=start + timedelta(seconds=index), size=20) for index in range(40)]
    chunks = segment_messages(
        messages,
        direct_chars=1_000,
        target_chars=1_000,
        hard_chars=50_000,
        overlap_messages=8,
    )
    assert len(chunks) > 1
    assert len(set(chunks[0].message_ids) & set(chunks[1].message_ids)) == 8
    assert "消息39" in chunks[-1].text


def test_oversized_single_message_splits_by_paragraph_and_keeps_source_id():
    message = PromptMessage(
        "oversized",
        datetime(2026, 8, 21, 9, 0),
        "成员",
        ("第一段内容" * 300) + "\n" + ("第二段内容" * 300),
    )
    chunks = segment_messages([message], direct_chars=1_000, target_chars=1_000, hard_chars=1_200)
    assert len(chunks) > 1
    assert all(chunk.context_chars <= 1_200 for chunk in chunks)
    assert all(chunk.message_ids == ("oversized",) for chunk in chunks)
    assert "#片段" in chunks[0].text


def test_event_json_rejects_invalid_and_normalizes_fragment_id():
    chunk = segment_messages(
        [PromptMessage("source", datetime(2026, 8, 21, 9, 0), "成员", "内容" * 800)],
        direct_chars=1_000,
        hard_chars=1_200,
    )[0]
    with pytest.raises(ValueError, match="有效 JSON"):
        parse_event_cards("not-json", chunk)

    raw = json.dumps({
        "events": [{
            "title": "事件",
            "people": ["成员"],
            "content": "真实内容",
            "quotes": ["原话"],
            "message_ids": ["source#片段1/2"],
        }]
    }, ensure_ascii=False)
    cards = parse_event_cards(raw, chunk)
    assert cards[0]["message_ids"] == ["source"]


def test_overlap_events_are_deduplicated_by_source_message():
    cards = deduplicate_event_cards([
        [{"title": "事件", "content": "过程", "message_ids": ["m1", "m2"]}],
        [{"title": "事件续", "content": "过程续", "message_ids": ["m2", "m3"]}],
    ])
    assert cards[0]["message_ids"] == ["m1", "m2"]
    assert cards[1]["message_ids"] == ["m3"]


@pytest.mark.parametrize("first_status", [429, 503])
def test_deepseek_retries_429_and_503_with_thinking_disabled(monkeypatch, first_status):
    calls: list[dict] = []

    class Response:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.text = "busy"

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(_url, *, headers, json, timeout):
        calls.append(json)
        return Response(first_status if len(calls) == 1 else 200)

    monkeypatch.setattr("app.providers.ai.deepseek.httpx.post", fake_post)
    monkeypatch.setattr("app.providers.ai.deepseek.time.sleep", lambda _delay: None)
    monkeypatch.setattr("app.providers.ai.deepseek.random.uniform", lambda _a, _b: 0)
    provider = DeepSeekV4FlashProvider(Settings(_env_file=None, ai_api_key="fake", ai_max_retries=3))
    assert provider._chat([{"role": "user", "content": "test"}], response_format="json_object") == "ok"
    assert len(calls) == 2
    assert calls[-1]["model"] == "deepseek-v4-flash"
    assert calls[-1]["thinking"] == {"type": "disabled"}
    assert calls[-1]["response_format"] == {"type": "json_object"}
