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


@pytest.mark.parametrize("candidate_count", [5, 6, 7])
def test_selection_keeps_five_to_seven_high_quality_topics(candidate_count: int):
    messages = _messages(candidate_count * 2)
    candidates = [
        _candidate(index, [f"m{(index - 1) * 2}", f"m{(index - 1) * 2 + 1}"], 36 - index, 18, 18)
        for index in range(1, candidate_count + 1)
    ]
    selection = score_and_select_topics(candidates, messages)
    assert selection["selected_count"] == candidate_count
    assert selection["thresholds"]["target_selected"] == 5
    assert selection["thresholds"]["max_selected"] == 7
    selected_ranks = [item["rank"] for item in selection["candidates"] if item["selected"]]
    assert selected_ranks == list(range(1, candidate_count + 1))


def test_selection_stops_at_seven_topics():
    messages = _messages(20)
    selection = score_and_select_topics(
        [
            _candidate(index, [f"m{(index - 1) * 2}", f"m{(index - 1) * 2 + 1}"], 38, 18, 18)
            for index in range(1, 11)
        ],
        messages,
    )
    assert selection["candidate_count"] == 10
    assert selection["selected_count"] == 7


def test_two_or_three_real_candidates_are_all_selected_without_padding():
    selection = score_and_select_topics(
        [_candidate(1, ["m0"]), _candidate(2, ["m1"]), _candidate(3, ["m2"], 0, 0)],
        _messages(3),
    )
    assert selection["selected_count"] == 3
    assert [item["selected"] for item in selection["candidates"]] == [True, True, True]


def test_high_volume_chat_does_not_reduce_topic_density():
    messages = _messages(205)
    candidates = [
        _candidate(index, [f"m{(index - 1) * 2}", f"m{(index - 1) * 2 + 1}"], comedy=36 - index, visual=18, recognition=18)
        for index in range(1, 8)
    ]
    selection = score_and_select_topics(candidates, messages)
    assert selection["selected_count"] == 7
    assert selection["thresholds"]["high_volume_message_threshold"] == 200


def test_single_candidate_is_selected_without_splitting_or_padding():
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1"])], _messages(2)
    )

    assert selection["selected_count"] == 1
    assert selection["selected_topic_ids"] == ["topic-01"]


def test_single_message_keeps_one_real_topic_without_fabricating_second_topic():
    selection = score_and_select_topics([_candidate(1, ["m0"])], _messages(1))

    assert selection["selected_count"] == 1
    assert selection["candidates"][0]["evidence_message_count"] == 1


def test_high_scoring_political_topic_is_blocked_and_safe_topic_is_promoted():
    political = _candidate(1, ["m0", "m1"], comedy=40, visual=20, recognition=20)
    political.update(title="总统大选", summary="围绕总统大选展开讨论。")
    safe = _candidate(2, ["m2", "m3"], comedy=20, visual=10, recognition=10)

    selection = score_and_select_topics([political, safe], _messages(4))
    by_id = {item["topic_id"]: item for item in selection["candidates"]}

    assert selection["selected_topic_ids"] == ["topic-02"]
    assert selection["safe_candidate_count"] == 1
    assert selection["blocked_topic_ids"] == ["topic-01"]
    assert selection["political_keyword_hits"] == [
        {
            "topic_id": "topic-01",
            "title": "总统大选",
            "matched_keywords": ["大选", "总统"],
        }
    ]
    assert by_id["topic-01"]["rank"] == 1
    assert by_id["topic-01"]["selected"] is False
    assert by_id["topic-01"]["image_eligible"] is False
    assert set(by_id["topic-01"]["political_keyword_matches"]) == {"总统", "大选"}
    assert by_id["topic-02"]["eligible_rank"] == 1


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("实弹卡弹成真伪证明", "射击体验中遇到卡弹。"),
        ("拿破仑轶事", "群友分享一个只含人物姓名的历史故事。"),
        ("公司主席投票", "公司内部投票讨论由谁主持会议。"),
        ("周末 party", "群友商量周末聚会安排。"),
        ("company party", "The company party starts after work."),
    ],
)
def test_non_political_or_broad_terms_are_not_blocked(title: str, summary: str):
    candidate = _candidate(1, ["m0"])
    candidate.update(title=title, summary=summary)

    selection = score_and_select_topics([candidate], _messages(1))

    assert selection["selected_topic_ids"] == ["topic-01"]
    assert selection["blocked_topic_ids"] == []
    assert selection["candidates"][0]["image_eligible"] is True


def test_explicit_english_political_term_is_blocked():
    candidate = _candidate(1, ["m0"])
    candidate.update(
        title="Ruling party election",
        summary="The ruling party prepared for a general election.",
    )

    selection = score_and_select_topics([candidate], _messages(1))

    assert selection["selected_count"] == 0
    assert set(selection["candidates"][0]["political_keyword_matches"]) == {
        "ruling party",
        "general election",
    }


def test_verified_evidence_text_is_checked_for_political_keywords():
    messages = _messages(1)
    messages[0] = PromptMessage(
        messages[0].message_id,
        messages[0].timestamp,
        messages[0].sender_name,
        "讨论全国人大相关消息",
        messages[0].sender_id,
    )

    selection = score_and_select_topics([_candidate(1, ["m0"])], messages)

    assert selection["selected_count"] == 0
    assert selection["candidates"][0]["political_keyword_matches"] == ["全国人大"]


def test_all_political_candidates_keep_audit_data_and_stop_prompt_selection():
    first = _candidate(1, ["m0"])
    first.update(title="总统竞选", summary="总统竞选相关讨论。")
    second = _candidate(2, ["m1"])
    second.update(title="议会选举", summary="议会选举相关讨论。")

    selection = score_and_select_topics([first, second], _messages(2))

    assert selection["candidate_count"] == 2
    assert selection["safe_candidate_count"] == 0
    assert selection["blocked_candidate_count"] == 2
    assert selection["selected_count"] == 0
    with pytest.raises(TopicSelectionError, match="TOPIC_CANDIDATES_POLITICAL"):
        selected_topics_json(selection)


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
    assert '"evidence_dialogue"' in payload


def test_model_supplied_quotes_are_ignored_and_rebuilt_from_message_ids():
    candidates = [_candidate(1, ["m0"]), _candidate(2, ["m1"])]
    candidates[0]["quotes"] = ["这句根本没有出现在聊天里"]
    candidates[1]["quotes"] = ["消息，1！"]
    selection = score_and_select_topics(candidates, _messages(2))
    by_id = {item["topic_id"]: item for item in selection["candidates"]}

    assert by_id["topic-01"]["quotes"] == ["消息0"]
    assert by_id["topic-01"]["evidence_dialogue"] == [
        {
            "message_id": "m0",
            "sender_id": "wxid-0",
            "speaker": "成员0",
            "text": "消息0",
            "original_text": "消息0",
        }
    ]
    assert by_id["topic-02"]["quotes"] == ["消息1"]
    assert by_id["topic-02"]["people"] == ["成员1"]


def test_candidate_prompt_requires_message_ids_not_model_supplied_names_or_quotes():
    from app.ai.topic_selection import TOPIC_CANDIDATE_SYSTEM, build_direct_candidate_prompt
    from app.ai.conversation_segments import ConversationChunk

    prompt = build_direct_candidate_prompt(
        ConversationChunk("聊天内容", ("m1",), "开始", "结束", 4)
    )

    assert "不得输出 people、quotes 或任何人物姓名" in TOPIC_CANDIDATE_SYSTEM
    assert '"people"' not in prompt
    assert '"quotes"' not in prompt


def test_visible_participants_are_derived_from_evidence_and_names_are_not_truncated():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [
        PromptMessage("m0", start, "很长但必须完整保留的群友姓名", "发起", "wxid-a"),
        PromptMessage("m1", start + timedelta(minutes=1), "很长但必须完整保留的群友姓名", "继续", "wxid-a"),
        PromptMessage("m2", start + timedelta(minutes=2), "李四", "回应", "wxid-b"),
        PromptMessage("m3", start + timedelta(minutes=3), "王五", "补充", "wxid-c"),
        PromptMessage("m4", start + timedelta(minutes=4), "赵六", "收尾", "wxid-d"),
    ]
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1", "m2", "m3", "m4"]), _candidate(2, ["m2"])],
        messages,
    )
    first = selection["candidates"][0]
    assert first["visible_participants"][0] == "很长但必须完整保留的群友姓名"
    assert "很长但必须完整保留的群友姓名" in first["participant_label"]
    assert first["participant_label"] == "很长但必须完整保留的群友姓名、李四、王五、赵六"
    assert set(first["visible_participants"]).issubset(set(first["participants"]))


def test_visible_participants_skip_an_over_budget_name_and_keep_scanning():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [
        PromptMessage("m0", start, "c2341298", "发起", "wxid-a"),
        PromptMessage("m1", start + timedelta(minutes=1), "c2341298", "继续", "wxid-a"),
        PromptMessage("m2", start + timedelta(minutes=2), "这是一个特别特别特别长的完整群友姓名", "回应", "wxid-b"),
        PromptMessage("m3", start + timedelta(minutes=3), "这是一个特别特别特别长的完整群友姓名", "补充", "wxid-b"),
        PromptMessage("m4", start + timedelta(minutes=4), "Max", "收尾", "wxid-c"),
        PromptMessage("m5", start + timedelta(minutes=5), "另一位群友", "另一主题", "wxid-d"),
    ]
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1", "m2", "m3", "m4"]), _candidate(2, ["m5"])],
        messages,
    )
    first = selection["candidates"][0]

    assert first["visible_participants"] == ["c2341298", "这是一个特别特别特别长的完整群友姓名", "Max"]
    assert first["participant_label"] == "c2341298、这是一个特别特别特别长的完整群友姓名、Max"


def test_same_display_name_with_different_ids_keeps_all_participant_fields_consistent():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [
        PromptMessage("m0", start, "同名", "发起", "wxid-a"),
        PromptMessage("m1", start + timedelta(minutes=1), "同名", "回应", "wxid-b"),
        PromptMessage("m2", start + timedelta(minutes=2), "第三人", "另一主题", "wxid-c"),
    ]
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1"]), _candidate(2, ["m2"])],
        messages,
    )
    first = next(item for item in selection["candidates"] if item["topic_id"] == "topic-01")

    assert first["participant_count"] == 2
    assert len(first["participants"]) == 2
    assert len(first["visible_participants"]) == 2
    assert all(name.startswith("同名（同名 ") for name in first["participants"])
    assert set(first["visible_participants"]) == set(first["participants"])
    assert "等 2 人" not in first["participant_label"]


def test_sender_id_case_variants_are_one_identity_in_topic_stats():
    start = datetime(2026, 8, 21, 9, 0)
    messages = [
        PromptMessage("m0", start, "同一人", "第一条", "WXID-A"),
        PromptMessage("m1", start + timedelta(minutes=1), "同一人", "第二条", "wxid-a"),
        PromptMessage("m2", start + timedelta(minutes=2), "另一人", "第三条", "wxid-b"),
    ]
    selection = score_and_select_topics(
        [_candidate(1, ["m0", "m1"]), _candidate(2, ["m2"])],
        messages,
    )
    first = next(
        item for item in selection["candidates"] if item["topic_id"] == "topic-01"
    )

    assert first["participant_count"] == 1
    assert first["participants"] == ["同一人"]


def test_unresolved_participant_uses_explicit_fallback_instead_of_fake_name():
    messages = [
        PromptMessage("m0", datetime(2026, 8, 21, 9, 0), "(未知)", "发言", "wxid-a"),
        PromptMessage("m1", datetime(2026, 8, 21, 9, 1), "未命名成员-abcd", "回应", "wxid-b"),
    ]
    selection = score_and_select_topics(
        [_candidate(1, ["m0"]), _candidate(2, ["m1"])],
        messages,
    )
    assert all(
        item["participant_label"] == "群友（昵称未识别）"
        for item in selection["candidates"]
    )


def test_duplicate_message_id_is_rejected_instead_of_overwriting_evidence():
    messages = _messages(2)
    messages[1] = PromptMessage(
        "m0",
        messages[1].timestamp,
        "另一成员",
        "另一条消息",
        "wxid-other",
    )

    with pytest.raises(TopicSelectionError, match="重复 message_id"):
        score_and_select_topics(
            [_candidate(1, ["m0"]), _candidate(2, ["m0"])],
            messages,
        )
