"""V2 排行榜文本渲染器。

P2：最简实现（内嵌格式），保证 ranking.txt 可交付；
P3：改为模板系统（templates/ranking/，前端可编辑），本模块重构为
RankingRenderer，渲染逻辑由模板控制。
"""

from __future__ import annotations

from app.ranking.engine_types import RankingResult


class RankingRenderer:
    """把 RankingResult 渲染成可发送的文本。"""

    def render_simple(self, result: RankingResult) -> str:
        """最简格式（P2 临时，P3 替换为模板）。"""
        lines = [
            result.group_name,
            "消息统计",
            "------------",
            "",
            f"时间起：{result.period_start}",
            f"时间止：{result.period_end}",
            "",
            "------------",
            "",
            f"发言人数：{result.speaker_count}",
            f"总消息：{result.message_count}",
            "",
            "------------",
            "",
            "发言 Top10",
        ]
        for s in result.top_speakers:
            lines.append(f"{s.rank}.{s.name}【{s.count}】")
        return "\n".join(lines)
