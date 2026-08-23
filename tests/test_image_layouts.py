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
    layout_plan_json,
    parse_layout_plan,
    preferred_layout_from_style,
    resolved_layout_instruction,
    restored_layout_plan,
)


def _topic_ids(count: int = 5) -> list[str]:
    return [f"topic-{index:02d}" for index in range(1, count + 1)]


def _raw_plan(count: int = 5, *, layout_id: str = "hero_with_insets") -> str:
    topic_ids = _topic_ids(count)
    beats = [
        {
            "topic_id": topic_id,
            "shots": ["establishing", "punchline"] if index == 0 else ["dialogue"],
        }
        for index, topic_id in enumerate(topic_ids)
    ]
    return json.dumps(
        {
            "layout_id": layout_id,
            "structure_mode": "hero_rhythm",
            "featured_topic_ids": [topic_ids[0]],
            "topic_order": topic_ids,
            "panel_beats": beats,
            "comedy_device": "反差",
            "layout_reason": "主话题有连续动作和反应",
        },
        ensure_ascii=False,
    )


def test_catalog_contains_distinct_comic_panel_grammars():
    assert LAYOUT_CATALOG_VERSION == "comic-panels-v3"
    assert len(IMAGE_LAYOUT_DEFINITIONS) == 8
    assert len({item.size_signature for item in IMAGE_LAYOUT_DEFINITIONS.values()}) == 8
    assert {"hero_with_insets", "nested_reactions", "diagonal_burst", "sidecar_scroll"}.issubset(
        IMAGE_LAYOUT_DEFINITIONS
    )
    for definition in IMAGE_LAYOUT_DEFINITIONS.values():
        combined = definition.instruction + definition.reading_path
        assert definition.key
        assert definition.label
        assert definition.size_signature
        assert "等大圆角卡片" not in combined
        assert any(word in combined for word in ("大格", "宽格", "竖格", "特写", "跨格", "尺寸"))


@pytest.mark.parametrize("count", [5, 6, 7])
def test_parse_accepts_five_to_seven_topics_and_expands_panel_count(count: int):
    plan = parse_layout_plan(_raw_plan(count), _topic_ids(count))
    assert list(plan.topic_order) == _topic_ids(count)
    assert plan.panel_count == count + 1
    assert any(len(beat.shots) > 1 for beat in plan.panel_beats)
    assert len(plan.panel_beats) == count
    assert plan.to_meta()["panel_size_signature"] == "XL-L-M-S-S-INSET-HERO"
    assert json.loads(layout_plan_json(plan))["panel_count"] == count + 1


def test_panel_beats_must_cover_every_topic_and_include_a_second_shot():
    payload = json.loads(_raw_plan())
    payload["panel_beats"] = payload["panel_beats"][:-1]
    with pytest.raises(LayoutPlanError, match="恰好覆盖"):
        parse_layout_plan(json.dumps(payload, ensure_ascii=False), _topic_ids())

    payload = json.loads(_raw_plan())
    for beat in payload["panel_beats"]:
        beat["shots"] = ["dialogue"]
    with pytest.raises(LayoutPlanError, match="总镜头数"):
        parse_layout_plan(json.dumps(payload, ensure_ascii=False), _topic_ids())


def test_previous_layout_is_avoided_unless_user_explicitly_locked_it():
    with pytest.raises(LayoutPlanError, match="连续重复"):
        parse_layout_plan(
            _raw_plan(),
            _topic_ids(),
            previous_layout_id="hero_with_insets",
        )
    locked = parse_layout_plan(
        _raw_plan(),
        _topic_ids(),
        previous_layout_id="hero_with_insets",
        style_layout_locked=True,
    )
    assert locked.layout_id == "hero_with_insets"


def test_custom_comic_layout_maps_without_reintroducing_scene_metaphors():
    custom = "蓝白水彩，采用大格加嵌套格的漫画分镜"
    assert detect_explicit_style_layout(custom) is True
    assert preferred_layout_from_style(custom) == "nested_reactions"
    plan = fixed_layout_plan("nested_reactions", _topic_ids(5))
    instruction = resolved_layout_instruction(plan, custom)
    assert plan.style_layout_locked is True
    assert plan.panel_count == 6
    assert "嵌套" in instruction
    assert "第1段剧情" in instruction
    assert "topic-" not in instruction


def test_fallback_avoids_recent_three_and_keeps_unequal_storyboard():
    recent = [
        {"layout_id": "hero_with_insets", "comedy_device": "反差"},
        {"layout_id": "staggered_mosaic", "comedy_device": "回环"},
        {"layout_id": "cinematic_strips", "comedy_device": "字面化"},
    ]
    plan = fallback_layout_plan(_topic_ids(7), recent_history=recent, seed_text="group|2026-08-24")
    assert plan.layout_id not in {item["layout_id"] for item in recent}
    assert plan.panel_count == 8
    assert plan.featured_topic_ids == ("topic-01",)
    instruction = resolved_layout_instruction(plan)
    assert "至少三级尺寸差" in instruction
    assert "等高矩形" in instruction
    assert "topic-" not in instruction


def test_only_current_catalog_with_valid_panel_beats_is_restored():
    plan = parse_layout_plan(_raw_plan(5, layout_id="split_focus"), _topic_ids(5))
    meta = plan.to_meta()
    restored = restored_layout_plan(meta, _topic_ids(5), style_layout_locked=False)
    assert restored is not None
    assert restored.reused is True
    assert restored.panel_count == 6

    old_meta = {**meta, "layout_catalog_version": "poster-layout-v2"}
    assert restored_layout_plan(old_meta, _topic_ids(5), style_layout_locked=False) is None

    invalid_meta = {**meta, "panel_beats": meta["panel_beats"][:-1]}
    assert restored_layout_plan(invalid_meta, _topic_ids(5), style_layout_locked=False) is None


def test_more_than_seven_topics_is_rejected():
    with pytest.raises(LayoutPlanError, match="2～7"):
        fallback_layout_plan(_topic_ids(8))
