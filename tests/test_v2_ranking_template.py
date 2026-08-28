"""V2 P3：排行榜模板系统单元测试。

覆盖：模板渲染 / 动态 Top 上限 / 旧 top10_lines 兼容 / 未知变量报错 /
模板服务 CRUD / 恢复默认 / default 不可删除 / Group 扩展字段。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import create_engine

from app.db import repository as repo
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
        group_name="示例交流群 A",
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
    expected = """===== 示例交流群 A =====

【发言排行榜】

示例交流群 A
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
    assert "【示例交流群 A】" in text
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


def test_render_text_primary_shows_text_and_interactions():
    result = RankingResult(
        group_name="Eason张UED-4群🤘",
        period_start="2026-08-28 00:00:00",
        period_end="2026-08-28 08:14:59",
        speaker_count=2,
        message_count=4,
        count_policy="text_primary_with_interactions",
        text_message_count=2,
        interaction_message_count=2,
        text_speaker_count=2,
        top_speakers=[
            TopSpeaker(
                rank=1,
                name="深圳-UI-白白",
                count=1,
                text_count=1,
                interaction_count=2,
                name_source="wechat_data_analysis",
            )
        ],
    )

    text = render_ranking(result, "{{top_lines}}")

    assert text == "1.深圳-UI-白白【文字 1｜互动 2】"


def test_text_interactions_template_appends_approved_explanation_once():
    result = RankingResult(
        group_name="测试群",
        period_start="2026-08-28 00:00:00",
        period_end="2026-08-28 23:59:59",
        speaker_count=1,
        message_count=3,
        count_policy="text_primary_with_interactions",
        text_message_count=1,
        interaction_message_count=2,
        text_speaker_count=1,
        top_speakers=[
            TopSpeaker(
                rank=1,
                name="群友",
                count=1,
                text_count=1,
                interaction_count=2,
                name_source="wechat_data_analysis",
            )
        ],
    )

    text = RankingRenderer().render(result, template_name="text_interactions")
    explanation = "说明：互动指图片、表情、引用等非文字消息，仅展示活跃度，不影响排名。"

    assert text.count(explanation) == 1
    assert text.rstrip().endswith(explanation)
    assert text.index("1.群友【文字 1｜互动 2】") < text.index(explanation)
    assert explanation not in render_ranking(result, DEFAULT_RANKING_TEMPLATE)


def test_render_top15_heading_and_lines():
    result = RankingResult(
        group_name="周末群",
        period_start="2026-08-14 00:00:00",
        period_end="2026-08-16 23:59:59",
        speaker_count=20,
        message_count=210,
        top_limit=15,
        top_speakers=[
            TopSpeaker(rank=i, name=f"成员{i:02}", count=21 - i)
            for i in range(1, 16)
        ],
    )
    text = render_ranking(result, DEFAULT_RANKING_TEMPLATE)
    assert "发言 Top15" in text
    assert "15.成员15【6】" in text


def test_render_unknown_variable_raises():
    with pytest.raises(TemplateError) as e:
        validate_template("{{group_name}} {{unknown_var}}")
    assert "unknown_var" in str(e.value)


def test_render_utf8_emoji():
    # 含 Emoji 的中文群名应原样渲染（UTF-8 支持）
    emoji_result = RankingResult(
        group_name="示例交流群 A ✨",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        speaker_count=27,
        message_count=409,
        top_speakers=[TopSpeaker(rank=1, name="示例UED-4群🤘", count=8)],
    )
    text = render_ranking(emoji_result, "{{group_name}}\n{{top10_lines}}")
    assert "✨" in text
    assert "示例UED-4群🤘" in text


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
    assert text == "示例交流群 A 共 409 条"


# ---------- Group 模型扩展 ----------


def test_group_v2_defaults():
    g = Group()
    assert g.schedule_rule == "weekday_default"
    assert g.send_time == "08:30"
    assert g.summary_model == "gpt-5.6-sol"
    assert g.prompt_model == "gpt-5.6-sol"
    assert g.image_enabled is True
    assert g.send_target == ""
    assert g.ranking_template == "default"
    assert g.ranking_count_policy == "all_messages"
    assert g.sender_name_policy == "resolved"
    assert g.image_prompt_template == "default"
    assert g.image_theme == "ai_free"
    assert g.image_theme_custom == ""


def test_existing_groups_table_gets_v2_policy_columns(tmp_path, monkeypatch):
    """旧数据库启动时应补列，并为已有群写入安全默认值。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE groups (id INTEGER PRIMARY KEY, display_name VARCHAR(128) NOT NULL DEFAULT '')"
        )
        conn.exec_driver_sql("INSERT INTO groups (id, display_name) VALUES (1, '旧群')")

    monkeypatch.setattr(repo, "engine", engine)
    repo._migrate_group_v2_columns()

    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(groups)")}
        row = conn.exec_driver_sql(
            "SELECT image_theme, image_theme_custom, ranking_count_policy, "
            "sender_name_policy FROM groups WHERE id = 1"
        ).one()
    assert {
        "image_theme",
        "image_theme_custom",
        "ranking_count_policy",
        "sender_name_policy",
    }.issubset(columns)
    assert tuple(row) == ("ai_free", "", "all_messages", "resolved")
