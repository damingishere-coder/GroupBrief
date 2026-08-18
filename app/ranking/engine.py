"""V2 确定性排行榜引擎接口（P2 实现）。

数字统计必须由代码完成，禁止 LLM 参与。V2 RankingEngine 在 V1
`app/services/ranking_service.py` 之上独立，输出结构化 ranking.json
（P3 由 RankingRenderer 渲染成 ranking.txt）。
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
    """V2 排行榜结构化结果（对应路线文档 JSON 结构）。"""

    group_name: str
    period_start: str
    period_end: str
    speaker_count: int
    message_count: int
    top_speakers: list[TopSpeaker] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "group_name": self.group_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "speaker_count": self.speaker_count,
            "message_count": self.message_count,
            "top_speakers": [s.to_dict() for s in self.top_speakers],
        }


class RankingEngine:
    """按已标准化消息计算排行榜。P2 实现。"""

    def compute(
        self,
        messages: list,
        group_name: str,
        period_start: str,
        period_end: str,
    ) -> RankingResult:
        raise NotImplementedError
