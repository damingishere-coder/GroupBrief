"""V2 确定性排行榜引擎。

- 统计完全由代码完成，禁止 LLM 参与；
- 过滤系统消息（复用 V1 规则：message_type=system 或系统内容关键词）；
- 可计数消息类型与 V1 保持一致（text/image/emoji/voice/video/file/link/quote/red_packet/transfer/other）；
- 排序规则确定性：按消息数降序，同数量按发送者名称稳定升序；
- 输出结构化 RankingResult（to_dict 即 ranking.json）。
"""

from __future__ import annotations

from collections import Counter
import hashlib

from app.data_sources.base import V2Message
from app.ranking.policies import (
    RANKING_POLICY_TEXT_PRIMARY,
    normalize_ranking_policy,
    normalize_sender_name_policy,
)
from app.services.message_normalizer import COUNTABLE_TYPES, SYSTEM_KEYWORDS
from app.services.speaker_identity import (
    build_speaker_stats,
    speaker_identity_key,
    speaker_name_sort_key,
)
from app.ranking.engine_types import RankingResult, TopSpeaker


class RankingEngine:
    """按已标准化消息计算排行榜。"""

    @staticmethod
    def _countable(m: V2Message) -> bool:
        if m.message_type == "system":
            return False
        if any(kw in (m.content or "") for kw in SYSTEM_KEYWORDS):
            return False
        return m.message_type in COUNTABLE_TYPES

    def compute(
        self,
        messages: list[V2Message],
        group_name: str,
        period_start: str,
        period_end: str,
        top_limit: int = 10,
        count_policy: str = "all_messages",
        name_source: str = "resolved",
    ) -> RankingResult:
        if top_limit <= 0:
            raise ValueError("排行榜上限必须大于 0")

        policy = normalize_ranking_policy(count_policy)
        normalized_name_source = normalize_sender_name_policy(name_source)
        countable_messages = [message for message in messages if self._countable(message)]
        speakers = build_speaker_stats(
            (message.sender_id, message.sender_name) for message in countable_messages
        )
        text_counts: Counter[tuple[str, str]] = Counter()
        interaction_counts: Counter[tuple[str, str]] = Counter()
        for message in countable_messages:
            key = speaker_identity_key(message.sender_id, message.sender_name)
            if key is None:
                continue
            if message.message_type == "text":
                text_counts[key] += 1
            else:
                interaction_counts[key] += 1

        message_count = len(countable_messages)
        speaker_count = len(speakers)
        text_message_count = sum(text_counts.values())
        interaction_message_count = sum(interaction_counts.values())
        text_speaker_count = len(text_counts)

        if policy == RANKING_POLICY_TEXT_PRIMARY:
            # 互动数只展示，不参与名次或同分排序。
            ordered = sorted(
                (item for item in speakers if text_counts[item.key] > 0),
                key=lambda item: (
                    -text_counts[item.key],
                    speaker_name_sort_key(item.name),
                    item.key,
                ),
            )
        else:
            ordered = sorted(
                speakers,
                key=lambda item: (-item.count, speaker_name_sort_key(item.name), item.key),
            )
        top_speakers = [
            TopSpeaker(
                rank=i + 1,
                name=item.name,
                count=(
                    text_counts[item.key]
                    if policy == RANKING_POLICY_TEXT_PRIMARY
                    else item.count
                ),
                identity_key=hashlib.sha256(
                    f"{item.key[0]}:{item.key[1]}".encode("utf-8")
                ).hexdigest()[:16],
                text_count=text_counts[item.key],
                interaction_count=interaction_counts[item.key],
                name_source=normalized_name_source,
            )
            for i, item in enumerate(ordered[:top_limit])
        ]

        return RankingResult(
            group_name=group_name,
            period_start=period_start,
            period_end=period_end,
            speaker_count=speaker_count,
            message_count=message_count,
            top_limit=top_limit,
            top_speakers=top_speakers,
            count_policy=policy,
            text_message_count=text_message_count,
            interaction_message_count=interaction_message_count,
            text_speaker_count=text_speaker_count,
        )
