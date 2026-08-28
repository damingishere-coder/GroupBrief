"""将群级发言人名称策略应用到已读取的消息。"""

from __future__ import annotations

from collections import defaultdict

from app.data_sources.base import V2Message
from app.providers.history.wechat_data_analysis import (
    _anonymous_sender_name,
    _sanitize_sender_name,
    _usable_sender_name,
)
from app.ranking.policies import (
    SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS,
    normalize_sender_name_policy,
)
from app.services.speaker_identity import speaker_identity_key


def apply_sender_name_policy(
    messages: list[V2Message],
    policy: object,
) -> list[V2Message]:
    """就地应用名称策略并返回消息；默认策略保持 Provider 结果不变。"""
    normalized = normalize_sender_name_policy(policy)
    if normalized != SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS:
        return messages

    latest_names: dict[tuple[str, str], str] = {}
    for message in sorted(messages, key=lambda item: (item.timestamp, item.message_id)):
        key = speaker_identity_key(message.sender_id, message.sender_name)
        if key is None:
            continue
        upstream = _sanitize_sender_name(
            message.upstream_sender_name
            or (
                message.sender_name
                if message.sender_name_source
                in {"", "wechat_data_analysis"}
                else ""
            )
        )
        if _usable_sender_name(upstream, str(message.sender_id or "")):
            latest_names[key] = upstream

    base_names: dict[tuple[str, str], str] = {}
    for message in messages:
        key = speaker_identity_key(message.sender_id, message.sender_name)
        if key is None or key in base_names:
            continue
        base_names[key] = latest_names.get(key) or _anonymous_sender_name(
            str(message.sender_id or message.sender_name or "unknown")
        )

    duplicate_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, name in base_names.items():
        duplicate_groups[name.casefold()].append(key)
    display_names = dict(base_names)
    for keys in duplicate_groups.values():
        if len(keys) <= 1:
            continue
        for number, key in enumerate(sorted(keys), start=1):
            display_names[key] = f"{base_names[key]}（同名 {number}）"

    for message in messages:
        key = speaker_identity_key(message.sender_id, message.sender_name)
        if key is None:
            continue
        message.sender_name = display_names[key]
        message.sender_name_source = (
            "wechat_data_analysis" if key in latest_names else "anonymous"
        )
    return messages
