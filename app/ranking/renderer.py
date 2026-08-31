"""V2 排行榜文本渲染器（模板驱动）。

从模板（templates/ranking/）渲染 ranking.txt，模板变量见
SUPPORTED_VARS。模板格式错误抛出 TemplateError，不导致服务崩溃。
"""

from __future__ import annotations

import re

from app.ranking.engine_types import RankingResult
from app.ranking.policies import RANKING_POLICY_TEXT_PRIMARY
from app.ranking.template_service import (
    SUPPORTED_VARS,
    TemplateError,
    RankingTemplateService,
    validate_template,
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_ranking(result: RankingResult, template_text: str) -> str:
    """把模板与统计结果渲染成最终排行榜文本。"""
    validate_template(template_text)

    if result.count_policy == RANKING_POLICY_TEXT_PRIMARY:
        top_lines = "\n".join(
            f"{s.rank}.{s.name}【文字 {s.text_count}｜互动 {s.interaction_count}】"
            for s in result.top_speakers
        )
    else:
        top_lines = "\n".join(
            f"{s.rank}.{s.name}【{s.count}】" for s in result.top_speakers
        )
    values = {
        "group_name": result.group_name,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "speaker_count": str(result.speaker_count),
        "message_count": str(result.message_count),
        "count_policy": result.count_policy,
        "text_message_count": str(result.text_message_count),
        "interaction_message_count": str(result.interaction_message_count),
        "text_speaker_count": str(result.text_speaker_count),
        "top_limit": str(result.top_limit),
        "top_lines": top_lines,
        # 兼容已有自定义模板；变量名虽为 top10，内容仍以本次实际上限为准。
        "top10_lines": top_lines,
    }

    def _replace(match: re.Match) -> str:
        var = match.group(1)
        return values.get(var, match.group(0))

    return _PLACEHOLDER_RE.sub(_replace, template_text)


class RankingRenderer:
    """按模板渲染 RankingResult。"""

    def __init__(self, service: RankingTemplateService | None = None):
        self.service = service or RankingTemplateService()

    def render(
        self,
        result: RankingResult,
        template_name: str = "default",
        template_text: str | None = None,
    ) -> str:
        """渲染 ranking.txt。template_text 优先（便于预览），否则读取模板文件。"""
        text = template_text
        if text is None:
            text = self.service.read(template_name)
        return render_ranking(result, text)

    # 兼容 P2 调用点：无模板场景的最简渲染（内容与 default 模板一致）
    def render_simple(self, result: RankingResult) -> str:
        from app.ranking.template_service import DEFAULT_RANKING_TEMPLATE

        return render_ranking(result, DEFAULT_RANKING_TEMPLATE)
