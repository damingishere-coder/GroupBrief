"""V2 P3：排行榜模板系统单元测试。

覆盖：模板渲染 / 变量替换 / top10_lines / 未知变量报错 /
模板服务 CRUD / 恢复默认 / default 不可删除 / Group 扩展字段。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import Group
from app.ranking.engine_types import RankingResult, TopSpeaker
from app.ranking.renderer import RankingRenderer, render_ranking
from app.ranking.template_service import (
    DEFAULT_RANKING_TEMPLATE,
    TemplateError,
    RankingTemplateService,
    validate_template,
)


def _result() -> RankingResult:
    return RankingResult(
        # 群名不含 emoji：默认模板自带 🐮🐴 装饰后缀（见路线文档 P3 默认模板）
        group_name="茶馆V3.0（三周年纪念）",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        speaker_count=27,
        message_count=409,
        top_speakers=[
            TopSpeaker(rank=1, name="停用", count=94),
            TopSpeaker(rank=2, name="罗斯", count=78),
            TopSpeaker(rank=3, name="啊菌菌阿菌", count=53),
            TopSpeaker(rank=4, name="杯面大英雄", count=39),
            TopSpeaker(rank=5, name="一颗苹果", count=35),
            TopSpeaker(rank=6, name="春夏秋冬", count=18),
            TopSpeaker(rank=7, name="梓木", count=18),
            TopSpeaker(rank=8, name="大明同学", count=17),
            TopSpeaker(rank=9, name="吉米多的围棋", count=7),
            TopSpeaker(rank=10, name="神奇小郭", count=7),
        ],
    )


# ---------- 渲染 ----------


def test_render_default_template_matches_expected():
    text = render_ranking(_result(), DEFAULT_RANKING_TEMPLATE)
    expected = """===== 茶馆V3.0（三周年纪念） =====

【发言排行榜】

茶馆V3.0（三周年纪念）
消息统计
------------

时间起：2026-08-17 00:00:00
时间止：2026-08-17 23:59:59

------------

发言人数：27

总消息：409

------------

发言 Top10
1.停用【94】
2.罗斯【78】
3.啊菌菌阿菌【53】
4.杯面大英雄【39】
5.一颗苹果【35】
6.春夏秋冬【18】
7.梓木【18】
8.大明同学【17】
9.吉米多的围棋【7】
10.神奇小郭【7】"""
    assert text.rstrip("\n") == expected


def test_render_variable_substitution():
    # 自定义模板，验证各变量替换
    tpl = "【{{group_name}}】\n{{period_start}}~{{period_end}}\n人数{{speaker_count}} 消息{{message_count}}\n{{top10_lines}}"
    text = render_ranking(_result(), tpl)
    assert "【茶馆V3.0（三周年纪念）】" in text
    assert "2026-08-17 00:00:00~2026-08-17 23:59:59" in text
    assert "人数27 消息409" in text
    assert "1.停用【94】" in text
    assert "10.神奇小郭【7】" in text


def test_render_top10_lines_format():
    tpl = "{{top10_lines}}"
    text = render_ranking(_result(), tpl)
    lines = text.split("\n")
    assert lines[0] == "1.停用【94】"
    assert lines[-1] == "10.神奇小郭【7】"


def test_render_unknown_variable_raises():
    with pytest.raises(TemplateError) as e:
        validate_template("{{group_name}} {{unknown_var}}")
    assert "unknown_var" in str(e.value)


def test_render_utf8_emoji():
    # 含 Emoji 的中文群名应原样渲染（UTF-8 支持）
    emoji_result = RankingResult(
        group_name="茶馆V3.0（三周年纪念）🐮🐴",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        speaker_count=27,
        message_count=409,
        top_speakers=[TopSpeaker(rank=1, name="Eason张UED-4群🤘", count=8)],
    )
    text = render_ranking(emoji_result, "{{group_name}}\n{{top10_lines}}")
    assert "🐮🐴" in text
    assert "Eason张UED-4群🤘" in text


# ---------- 模板服务 ----------


def _service(tmp_path: Path) -> RankingTemplateService:
    return RankingTemplateService(templates_dir=tmp_path / "ranking")


def test_service_creates_default(tmp_path):
    svc = _service(tmp_path)
    assert "default" in svc.list_templates()
    assert svc.read("default") == DEFAULT_RANKING_TEMPLATE


def test_service_save_and_read(tmp_path):
    svc = _service(tmp_path)
    content = "自定义：{{group_name}} / {{message_count}}"
    svc.save("custom", content)
    assert "custom" in svc.list_templates()
    assert svc.read("custom") == content


def test_service_save_invalid_template(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(TemplateError):
        svc.save("bad", "{{not_a_var}}")


def test_service_delete(tmp_path):
    svc = _service(tmp_path)
    svc.save("custom", "{{group_name}}")
    svc.delete("custom")
    assert "custom" not in svc.list_templates()


def test_service_default_cannot_delete(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(TemplateError):
        svc.delete("default")


def test_service_reset_default(tmp_path):
    svc = _service(tmp_path)
    svc.save("default", "改坏了 {{group_name}}")
    content = svc.reset("default")
    assert content == DEFAULT_RANKING_TEMPLATE
    assert svc.read("default") == DEFAULT_RANKING_TEMPLATE


def test_service_invalid_name(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(TemplateError):
        svc.read("../etc/passwd")
    with pytest.raises(TemplateError):
        svc.save("a b", "{{group_name}}")


def test_renderer_uses_template_file(tmp_path):
    svc = _service(tmp_path)
    svc.save("mytpl", "{{group_name}} 共 {{message_count}} 条")
    renderer = RankingRenderer(service=svc)
    text = renderer.render(_result(), template_name="mytpl")
    assert text == "茶馆V3.0（三周年纪念） 共 409 条"


# ---------- Group 模型扩展 ----------


def test_group_v2_defaults():
    g = Group()
    assert g.schedule_rule == "weekday_default"
    assert g.send_time == "08:30"
    assert g.summary_model == "deepseek-v4-flash"
    assert g.prompt_model == "deepseek-v4-flash"
    assert g.image_enabled is True
    assert g.send_target == ""
    assert g.ranking_template == "default"
    assert g.image_prompt_template == "default"
