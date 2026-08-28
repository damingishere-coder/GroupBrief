"""群级排行榜与发言人名称策略。"""

from __future__ import annotations


RANKING_POLICY_ALL_MESSAGES = "all_messages"
RANKING_POLICY_TEXT_PRIMARY = "text_primary_with_interactions"
RANKING_POLICIES = frozenset(
    {RANKING_POLICY_ALL_MESSAGES, RANKING_POLICY_TEXT_PRIMARY}
)

SENDER_NAME_POLICY_RESOLVED = "resolved"
SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS = "wechat_data_analysis"
SENDER_NAME_POLICIES = frozenset(
    {SENDER_NAME_POLICY_RESOLVED, SENDER_NAME_POLICY_WECHAT_DATA_ANALYSIS}
)


def normalize_ranking_policy(value: object) -> str:
    policy = str(value or RANKING_POLICY_ALL_MESSAGES).strip().lower()
    if policy not in RANKING_POLICIES:
        raise ValueError(f"不支持的排行榜统计口径：{value}")
    return policy


def normalize_sender_name_policy(value: object) -> str:
    policy = str(value or SENDER_NAME_POLICY_RESOLVED).strip().lower()
    if policy not in SENDER_NAME_POLICIES:
        raise ValueError(f"不支持的发言人名称策略：{value}")
    return policy


def uses_strict_image_fact_contract(ranking_policy: object) -> bool:
    return normalize_ranking_policy(ranking_policy) == RANKING_POLICY_TEXT_PRIMARY
