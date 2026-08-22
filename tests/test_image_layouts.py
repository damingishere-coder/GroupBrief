from __future__ import annotations

import json

import pytest

from app.ai.layouts import (
    IMAGE_LAYOUT_DEFINITIONS,
    LayoutPlanError,
    detect_explicit_style_layout,
    fallback_layout_plan,
    fixed_layout_plan,
    parse_layout_plan,
    preferred_layout_from_style,
    resolved_layout_instruction,
    restored_layout_plan,
)


def test_catalog_has_twelve_unique_style_neutral_whole_poster_layouts():
    assert len(IMAGE_LAYOUT_DEFINITIONS) == 12
    assert len(set(IMAGE_LAYOUT_DEFINITIONS)) == 12
    forbidden_style_terms = ("配色", "画材", "服装", "人物造型", "纹理", "光影", "画风")
    for key, definition in IMAGE_LAYOUT_DEFINITIONS.items():
        assert definition.key == key
        assert definition.label
        assert definition.best_for
        assert "整张海报" in definition.instruction
        assert not any(term in definition.instruction for term in forbidden_style_terms)


def test_layout_plan_must_cover_every_selected_topic_exactly_once():
    raw = json.dumps(
        {
            "layout_id": "group_court",
            "hero_topic_id": "topic-02",
            "support_topic_ids": ["topic-01", "topic-03"],
            "comedy_device": "一本正经地荒诞",
            "layout_reason": "存在真实争论",
        },
        ensure_ascii=False,
    )
    plan = parse_layout_plan(raw, ["topic-01", "topic-02", "topic-03"])
    assert plan.layout_id == "group_court"
    assert plan.hero_topic_id == "topic-02"
    assert set(plan.support_topic_ids) == {"topic-01", "topic-03"}

    duplicate = raw.replace('["topic-01", "topic-03"]', '["topic-01", "topic-01"]')
    with pytest.raises(LayoutPlanError, match="不得重复|恰好覆盖"):
        parse_layout_plan(duplicate, ["topic-01", "topic-02", "topic-03"])


def test_previous_layout_is_rejected_unless_user_style_locks_layout():
    raw = json.dumps(
        {
            "layout_id": "hero_cover",
            "hero_topic_id": "topic-01",
            "support_topic_ids": ["topic-02"],
            "comedy_device": "反差",
            "layout_reason": "主事件突出",
        },
        ensure_ascii=False,
    )
    with pytest.raises(LayoutPlanError, match="不得与前一次"):
        parse_layout_plan(raw, ["topic-01", "topic-02"], previous_layout_id="hero_cover")

    locked = parse_layout_plan(
        raw,
        ["topic-01", "topic-02"],
        previous_layout_id="hero_cover",
        style_layout_locked=True,
    )
    assert locked.style_layout_locked is True


def test_explicit_custom_style_layout_wins_and_instruction_keeps_style_priority():
    custom = "复古报纸三栏头版"
    assert detect_explicit_style_layout(custom) is True
    assert preferred_layout_from_style(custom) == "hero_cover"
    plan = fixed_layout_plan("hero_cover", ["topic-01", "topic-02"])
    instruction = resolved_layout_instruction(plan, custom)
    assert custom not in instruction  # 风格原文只存在于标准【大主题】段，便于后续安全切换
    assert "最高结构优先级" in instruction
    assert plan.layout_id in instruction


def test_fallback_avoids_recent_three_and_same_date_plan_can_be_restored():
    history = (
        {"layout_id": "hero_cover", "comedy_device": "字面化"},
        {"layout_id": "comic_strip", "comedy_device": "反差"},
        {"layout_id": "group_court", "comedy_device": "回环"},
    )
    plan = fallback_layout_plan(
        ["topic-01", "topic-02"],
        recent_history=history,
        seed_text="group-1|2026-08-22",
    )
    assert plan.layout_id not in {"hero_cover", "comic_strip", "group_court"}

    restored = restored_layout_plan(
        plan.to_meta(),
        ["topic-new", "topic-02"],
        style_layout_locked=False,
    )
    assert restored is not None
    assert restored.layout_id == plan.layout_id
    assert restored.reused is True
    assert restored.hero_topic_id in {"topic-new", "topic-02"}
