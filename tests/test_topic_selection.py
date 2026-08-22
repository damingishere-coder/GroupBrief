from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.ai.conversation_segments import PromptMessage
from app.ai.topic_selection import (
    TopicSelectionError,
    parse_topic_candidates,
    score_and_select_topics,
    selected_topics_json,
)


def _messages(count: int = 8) -> list[PromptMessage]:
    start = datetime(2026, 8, 21, 9, 0)
    return [
        PromptMessage(
            message_id=f"m{index}",
            timestamp=start + timedelta(minutes=index * 5),
            sender_name=f"成员{index % 4}",
            text=f"消息{index}",
            sender_id=f"wxid-{index % 4}",
        )
        for index in range(count)
    ]


def _candidate(
    index: int,
    ids: list[str],
    comedy: float = 30,
    visual: float = 15,
    recognition: float = 15,
) -> dict:
    return {
        "topic_id": f"topic-{index:02d}",
        "title": f"主题{index}",
        "summary": f"真实主题{index}",
        "people": [],
        "quotes": [],
        "start_time": "",
        "end_time": "",
        "message_ids": ids,
        "comedy_score": comedy,
        "group_recognition_score": recognition,
        "visual_score": visual,
        "comedy_angle": "真实反差",
        "visual_gag": "把真实原话做成视觉比喻",
        "score_reason": "真实证据",
    }


def test_score_weights_total_100_and_log_normalization():
    messages = _messages(8)
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1", "m2", "m3"]), _candidate(2, ["m4"])],
        messages,
    )
    assert sum(selection["weights"].values()) == 100
    first, second = selection["candidates"]
    assert selection["weights"]["comedy"] == 40
    assert first["scores"]["discussion"] == 10
    assert first["scores"]["participation"] == 5
    assert second["scores"]["discussion"] > 0
    assert second["scores"]["discussion"] < 10
    assert selection["selected_count"] == 2


def test_selection_stops_at_first_threshold_failure_and_max_five():
    messages = _messages(8)
    candidates = [
        _candidate(1, ["m0", "m1"], 40, 20, 20),
        _candidate(2, ["m2", "m3"], 38, 19, 19),
        _candidate(3, ["m4", "m5"], 35, 18, 18),
        _candidate(4, ["m6"], 0, 0),
        _candidate(5, ["m7"], 40, 20, 20),
    ]
    selection = score_and_select_topics(candidates, messages)
    assert 2 <= selection["selected_count"] <= 5
    selected_ranks = [item["rank"] for item in selection["candidates"] if item["selected"]]
    assert selected_ranks == list(range(1, len(selected_ranks) + 1))


def test_single_candidate_fails_instead_of_splitting_one_topic_to_fill_quota():
    with pytest.raises(TopicSelectionError, match="TOPIC_CANDIDATES_INSUFFICIENT"):
        score_and_select_topics([_candidate(1, ["m0", "m1"])], _messages(2))


def test_single_message_fails_without_fabricating_second_topic():
    with pytest.raises(TopicSelectionError, match="TOPIC_CANDIDATES_INSUFFICIENT"):
        score_and_select_topics([_candidate(1, ["m0"])], _messages(1))


def test_parse_filters_duplicate_and_invalid_message_ids():
    raw = """{"candidates":[{"title":"主题","summary":"描述","message_ids":["m0","bad","m0"],"comedy_score":50,"group_recognition_score":30,"visual_score":30,"comedy_angle":"反差","visual_gag":"字面化"}]}"""
    parsed = parse_topic_candidates(raw, ["m0"])
    assert parsed[0]["message_ids"] == ["m0"]
    assert parsed[0]["comedy_score"] == 40
    assert parsed[0]["group_recognition_score"] == 20
    assert parsed[0]["visual_score"] == 20


def test_parse_accepts_legacy_interestingness_score_for_one_version():
    raw = """{"candidates":[{"title":"主题","summary":"描述","message_ids":["m0"],"interestingness_score":25,"visual_score":15}]}"""
    parsed = parse_topic_candidates(raw, ["m0"])
    assert parsed[0]["comedy_score"] == 25


def test_selected_topics_json_contains_only_selected_candidates():
    selection = score_and_select_topics(
        [_candidate(1, ["m0"]), _candidate(2, ["m1"]), _candidate(3, ["m2"], 0, 0)],
        _messages(3),
    )
    payload = selected_topics_json(selection)
    assert '"selected":true' in payload
    assert '"selected":false' not in payload
