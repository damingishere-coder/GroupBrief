"""V2 排行榜数据结构（RankingResult / TopSpeaker）。

独立于引擎实现，供 RankingEngine（P2）与 RankingRenderer（P3）共同引用。
对应路线文档排行榜 JSON 结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TopSpeaker:
    rank: int
    name: str
    count: int

    def to_dict(self) -> dict:
        return {"rank": self.rank, "name": self.name, "count": self.count}


@dataclass
class RankingResult:
    """V2 排行榜结构化结果。"""

    group_name: str
    period_start: str
    period_end: str
    speaker_count: int
    message_count: int
    top_speakers: list[TopSpeaker] = field(default_factory=list)
    # 放在原有字段之后，保留旧代码按位置传入 top_speakers 的兼容性。
    top_limit: int = 10

    def to_dict(self) -> dict:
        return {
            "group_name": self.group_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "speaker_count": self.speaker_count,
            "message_count": self.message_count,
            "top_limit": self.top_limit,
            "top_speakers": [s.to_dict() for s in self.top_speakers],
        }
