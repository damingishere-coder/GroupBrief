"""群级 WeChatDataAnalysis 展示名策略。"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.data_sources.wechat_data_analysis import _to_v2_message
from app.providers.history.wechat_data_analysis import _mcp_to_raw, _to_raw
from app.services.sender_name_policy import apply_sender_name_policy


NOW = datetime(2026, 8, 28, 8, 0, 0)


def test_mcp_and_export_keep_same_sender_display_name():
    export = _to_raw(
        {
            "group_id": "group@chatroom",
            "group_name": "测试群",
            "sender_id": "wxid-a",
            "senderDisplayName": "  深\t圳-UI-白白\u200b  ",
            "content": "早",
        },
        NOW,
    )
    mcp = _mcp_to_raw(
        {
            "id": "mcp-1",
            "senderUsername": "wxid-a",
            "senderDisplayName": "  深\t圳-UI-白白\u200b  ",
            "renderType": "text",
            "content": "早",
        },
        "group@chatroom",
        NOW,
    )
    messages = [_to_v2_message(export), _to_v2_message(mcp)]

    apply_sender_name_policy(messages, "wechat_data_analysis")

    assert [message.sender_name for message in messages] == [
        "深 圳-UI-白白",
        "深 圳-UI-白白",
    ]
    assert {message.sender_name_source for message in messages} == {
        "wechat_data_analysis"
    }


def test_latest_valid_upstream_name_wins_and_contact_name_is_ignored():
    older = _to_v2_message(
        _mcp_to_raw(
            {
                "id": "m1",
                "senderUsername": "wxid-a",
                "senderDisplayName": "旧昵称",
                "renderType": "text",
            },
            "group@chatroom",
            NOW,
        )
    )
    newer = _to_v2_message(
        _mcp_to_raw(
            {
                "id": "m2",
                "senderUsername": "wxid-a",
                "senderDisplayName": "最新昵称",
                "renderType": "image",
            },
            "group@chatroom",
            NOW + timedelta(minutes=1),
        )
    )
    older.sender_name = "联系人备注"
    newer.sender_name = "联系人备注"
    older.sender_name_source = newer.sender_name_source = "contact"

    apply_sender_name_policy([older, newer], "wechat_data_analysis")

    assert older.sender_name == newer.sender_name == "最新昵称"
    assert older.sender_name_source == newer.sender_name_source == "wechat_data_analysis"


def test_missing_names_are_stable_and_same_names_are_disambiguated():
    raw_messages = [
        _mcp_to_raw(
            {
                "id": "m1",
                "senderUsername": "wxid-a",
                "senderDisplayName": "同名",
                "renderType": "text",
            },
            "group@chatroom",
            NOW,
        ),
        _mcp_to_raw(
            {
                "id": "m2",
                "senderUsername": "wxid-b",
                "senderDisplayName": "同名",
                "renderType": "text",
            },
            "group@chatroom",
            NOW,
        ),
        _mcp_to_raw(
            {
                "id": "m3",
                "senderUsername": "wxid-missing",
                "senderDisplayName": "",
                "renderType": "text",
            },
            "group@chatroom",
            NOW,
        ),
    ]
    messages = [_to_v2_message(message) for message in raw_messages]

    apply_sender_name_policy(messages, "wechat_data_analysis")

    assert messages[0].sender_name == "同名（同名 1）"
    assert messages[1].sender_name == "同名（同名 2）"
    assert messages[2].sender_name.startswith("未命名成员-")
    assert messages[2].sender_name_source == "anonymous"
