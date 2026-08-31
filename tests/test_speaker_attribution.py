from __future__ import annotations

from datetime import datetime

from app.ai.speaker_attribution import build_attribution_contract
from app.data_sources.base import V2Message


def _message(
    message_id: str,
    sender_id: str,
    sender_name: str,
    upstream_sender_name: str,
    *,
    content: str = "测试消息",
) -> V2Message:
    return V2Message(
        message_id=message_id,
        group_id="group@chatroom",
        group_name="测试群",
        sender_id=sender_id,
        sender_name=sender_name,
        upstream_sender_name=upstream_sender_name,
        sender_name_source="wechat_data_analysis",
        timestamp=datetime(2026, 8, 29, 9, 0),
        content=content,
    )


def test_unique_chat_time_name_wins_over_current_resolved_name():
    contract = build_attribution_contract(
        [_message("m1", "wxid-a", "狗莓是a仔", "杭州-UI-呱呱")]
    )

    assert contract.names[0].display_name == "杭州-UI-呱呱"
    assert contract.names[0].source == "upstream_sender_name"


def test_shared_bad_upstream_name_falls_back_to_resolved_names():
    contract = build_attribution_contract(
        [
            _message("m1", "wxid-a", "Alice", "c2341298"),
            _message("m2", "wxid-b", "Bob", "c2341298"),
        ]
    )

    assert [item.display_name for item in contract.names] == ["Alice", "Bob"]


def test_same_sender_keeps_each_messages_chat_time_name():
    contract = build_attribution_contract(
        [
            _message("m1", "wxid-a", "当前名", "当天早些时候"),
            _message("m2", "wxid-a", "当前名", "当天后来改名"),
        ]
    )

    assert [item.display_name for item in contract.names] == [
        "当天早些时候",
        "当天后来改名",
    ]


def test_same_sender_id_is_case_insensitive_for_upstream_collision_check():
    contract = build_attribution_contract(
        [
            _message("m1", "WXID-A", "当前名", "聊天名"),
            _message("m2", "wxid-a", "当前名", "聊天名"),
        ]
    )

    assert [item.display_name for item in contract.names] == ["聊天名", "聊天名"]


def test_snapshot_and_speaker_hashes_track_different_changes():
    original = [_message("m1", "wxid-a", "当前名", "聊天名", content="原文")]
    content_changed = [
        _message("m1", "wxid-a", "当前名", "聊天名", content="修改后的原文")
    ]
    name_changed = [
        _message("m1", "wxid-a", "当前名", "新的聊天名", content="原文")
    ]

    first = build_attribution_contract(original)
    second = build_attribution_contract(content_changed)
    third = build_attribution_contract(name_changed)

    assert first.message_snapshot_sha256 != second.message_snapshot_sha256
    assert first.speaker_fingerprint == second.speaker_fingerprint
    assert first.message_snapshot_sha256 != third.message_snapshot_sha256
    assert first.speaker_fingerprint != third.speaker_fingerprint
