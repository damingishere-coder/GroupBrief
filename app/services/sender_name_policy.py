"""将群级发言人名称策略应用到已读取的消息。"""

from __future__ import annotations

from app.data_sources.base import V2Message
from app.providers.history.wechat_data_analysis import (
    _sanitize_sender_name,
    _usable_sender_name,
)
from app.ranking.policies import (
    SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS,
    normalize_sender_name_policy,
)
from app.services.speaker_identity import build_speaker_stats, speaker_identity_key


def apply_sender_name_policy(
    messages: list[V2Message],
    policy: object,
) -> list[V2Message]:
    """就地应用名称策略并返回消息；默认策略保持 Provider 结果不变。"""
    normalized = normalize_sender_name_policy(policy)
    if normalized != SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS:
        return messages

    stats = build_speaker_stats(
        (
            message.sender_id,
            _sanitize_sender_name(message.sender_name),
            message.sender_name_source == "contact",
        )
        for message in messages
    )
    display_names = {item.key: item.name for item in stats}
    seen_keys: set[tuple[str, str]] = set()
    usable_keys: set[tuple[str, str]] = set()
    for message in messages:
        key = speaker_identity_key(message.sender_id, message.sender_name)
        if key is None:
            continue
        seen_keys.add(key)
        name = _sanitize_sender_name(message.sender_name)
        is_contact = message.sender_name_source == "contact"
        contact_usable = bool(
            is_contact
            and name
            and name.casefold() not in {"none", "null", "(未知)", "未知"}
        )
        if contact_usable or _usable_sender_name(name, str(message.sender_id or "")):
            usable_keys.add(key)
    anonymous_keys = seen_keys - usable_keys

    for message in messages:
        key = speaker_identity_key(message.sender_id, message.sender_name)
        if key is None:
            continue
        message.sender_name = display_names[key]
        if key in anonymous_keys:
            message.sender_name_source = "anonymous"
    return messages
