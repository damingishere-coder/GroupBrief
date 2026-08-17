"""确定性排行榜引擎。

禁止 LLM 参与数字统计。统计逻辑：
- 只统计 countable 消息（系统消息不计）
- 连续消息不合并，逐条计数
- Top10：按消息数降序；同数量按发送者名称（稳定排序）保证确定性
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.services.message_normalizer import NormalizedMessage


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
        counter: Counter[str] = Counter()
        for m in messages:
            if not m.countable:
                continue
            if not m.sender_name:
                continue
            counter[m.sender_name] += 1

        total = sum(counter.values())
        speakers = len(counter)

        ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        top10 = ordered[:10]

        return RankingResult(
            group_name=group_name,
            range_start=range_start,
            range_end=range_end,
            total_messages=total,
            speaker_count=speakers,
            top10=top10,
        )
