"""确定性排行榜引擎。

禁止 LLM 参与数字统计。统计逻辑：
- 只统计 countable 消息（系统消息不计）
- 连续消息不合并，逐条计数
- Top10：按消息数降序；同数量按发送者名称（稳定排序）保证确定性
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.message_normalizer import NormalizedMessage
from app.services.speaker_identity import build_speaker_stats, speaker_name_sort_key


@dataclass
class RankingResult:
    group_name: str
    range_start: str
    range_end: str
    total_messages: int
    speaker_count: int
    top10: list[tuple[str, int]] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            self.group_name,
            "消息统计",
            "------------",
            "",
            f"时间起：{self.range_start}",
            f"时间止：{self.range_end}",
            "",
            "------------",
            "",
            f"发言人数：{self.speaker_count}",
            f"总消息：{self.total_messages}",
            "",
            "------------",
            "",
            "发言 Top10",
        ]
        for idx, (name, count) in enumerate(self.top10, start=1):
            lines.append(f"{idx}.{name}【{count}】")
        return "\n".join(lines)


class RankingEngine:
    def compute(
        self,
        messages: list[NormalizedMessage],
        group_name: str,
        range_start: str,
        range_end: str,
    ) -> RankingResult:
        speaker_stats = build_speaker_stats(
            (m.sender_id, m.sender_name) for m in messages if m.countable
        )
        total = sum(item.count for item in speaker_stats)
        speakers = len(speaker_stats)

        ordered = sorted(
            speaker_stats,
            key=lambda item: (-item.count, speaker_name_sort_key(item.name), item.key),
        )
        top10 = [(item.name, item.count) for item in ordered[:10]]

        return RankingResult(
            group_name=group_name,
            range_start=range_start,
            range_end=range_end,
            total_messages=total,
            speaker_count=speakers,
            top10=top10,
        )
