"""V2 确定性排行榜引擎。

- 统计完全由代码完成，禁止 LLM 参与；
- 过滤系统消息（复用 V1 规则：message_type=system 或系统内容关键词）；
- 可计数消息类型与 V1 保持一致（text/image/emoji/voice/video/file/link/quote/red_packet/transfer/other）；
- 排序规则确定性：按消息数降序，同数量按发送者名称稳定升序；
- 输出结构化 RankingResult（to_dict 即 ranking.json）。
"""

from __future__ import annotations

from collections import Counter

from app.data_sources.base import V2Message
from app.services.message_normalizer import COUNTABLE_TYPES, SYSTEM_KEYWORDS
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
    ) -> RankingResult:
        counter: Counter[str] = Counter()
        for m in messages:
            if not self._countable(m):
                continue
            if not m.sender_name:
                continue
            counter[m.sender_name] += 1

        message_count = sum(counter.values())
        speaker_count = len(counter)

        # 确定性排序：消息数降序，同数量按名称稳定升序
        ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top_speakers = [
            TopSpeaker(rank=i + 1, name=name, count=count)
            for i, (name, count) in enumerate(ordered[:10])
        ]

        return RankingResult(
            group_name=group_name,
            period_start=period_start,
            period_end=period_end,
            speaker_count=speaker_count,
            message_count=message_count,
            top_speakers=top_speakers,
        )
