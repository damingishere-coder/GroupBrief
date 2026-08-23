from __future__ import annotations

import json

import pytest

from app.ai.layouts import (
    IMAGE_LAYOUT_DEFINITIONS,
    LAYOUT_CATALOG_VERSION,
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


@pytest.mark.parametrize(
    ("structure_mode", "featured"),
    [
        ("single_focus", ["topic-02"]),
        ("dual_focus", ["topic-01", "topic-02"]),
        ("equal_topics", []),
    ],
)
def test_layout_plan_supports_dynamic_structures_and_covers_topics_once(
    structure_mode, featured
):
    raw = json.dumps(
        {
            "layout_id": "group_court",
            "structure_mode": structure_mode,
            "featured_topic_ids": featured,
            "topic_order": ["topic-02", "topic-01", "topic-03"],
            "comedy_device": "一本正经地荒诞",
            "layout_reason": "存在真实争论",
        },
        ensure_ascii=False,
    )
    plan = parse_layout_plan(raw, ["topic-01", "topic-02", "topic-03"])
    assert plan.layout_id == "group_court"
    assert plan.structure_mode == structure_mode
    assert list(plan.featured_topic_ids) == featured
    assert plan.topic_order == ("topic-02", "topic-01", "topic-03")

    duplicate = raw.replace(
        '["topic-02", "topic-01", "topic-03"]',
        '["topic-02", "topic-01", "topic-01"]',
    )
    with pytest.raises(LayoutPlanError, match="不得重复|恰好覆盖"):
        parse_layout_plan(duplicate, ["topic-01", "topic-02", "topic-03"])


def test_structure_mode_requires_exact_featured_count():
    raw = json.dumps(
        {
            "layout_id": "newsroom_live",
            "structure_mode": "equal_topics",
            "featured_topic_ids": ["topic-01"],
            "topic_order": ["topic-01", "topic-02"],
            "comedy_device": "反差",
            "layout_reason": "并行热点",
        },
        ensure_ascii=False,
    )
    with pytest.raises(LayoutPlanError, match="必须包含 0 个重点话题"):
        parse_layout_plan(raw, ["topic-01", "topic-02"])


def test_previous_layout_is_rejected_unless_user_style_locks_layout():
    raw = json.dumps(
        {
            "layout_id": "hero_cover",
            "structure_mode": "dual_focus",
            "featured_topic_ids": ["topic-01", "topic-02"],
            "topic_order": ["topic-01", "topic-02"],
            "comedy_device": "反差",
            "layout_reason": "双话题对照",
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
    assert plan.structure_mode == "dual_focus"


def test_fallback_avoids_recent_three_and_uses_equal_topics():
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
    assert plan.structure_mode == "equal_topics"
    assert plan.featured_topic_ids == ()
    assert plan.topic_order == ("topic-01", "topic-02")

    restored = restored_layout_plan(
        plan.to_meta(),
        ["topic-01", "topic-02"],
        style_layout_locked=False,
    )
    assert restored is not None
    assert restored.layout_id == plan.layout_id
    assert restored.reused is True
    assert restored.structure_mode == "equal_topics"
    assert restored.topic_order == plan.topic_order
    assert restored.to_meta()["layout_catalog_version"] == LAYOUT_CATALOG_VERSION


def test_legacy_hero_support_meta_is_readable_but_not_reused_for_new_prompt():
    legacy = {
        "layout_id": "group_court",
        "hero_topic_id": "topic-01",
        "support_topic_ids": ["topic-02"],
        "comedy_device": "反差",
    }
    assert (
        restored_layout_plan(
            legacy,
            ["topic-01", "topic-02"],
            style_layout_locked=False,
        )
        is None
    )
