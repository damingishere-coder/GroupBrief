"""公开风格目录、每日确定性、历史恢复和旧主题兼容。"""

import re

from app.ai.image_themes import (
    STYLE_CATALOG_VERSION,
    STYLE_FAMILIES,
    STYLE_SAFETY_SUFFIX,
    public_image_theme_options,
    resolve_image_theme,
    validate_style_catalog,
)


EXPECTED_PRESET_KEYS = [
    "silkscreen_editorial",
    "paper_cut_layered",
    "watercolor_journal",
    "retro_futurism",
    "clay_stopmotion",
    "woodcut_editorial",
    "glassmorphism_tech",
    "children_science_picturebook",
    "architectural_blueprint",
    "textile_embroidery",
    "ink_wash_editorial",
    "art_deco_night",
    "isometric_miniature",
    "pixel_arcade",
    "cel_animation",
    "chibi_sticker",
    "pencil_storyboard",
    "natural_history_engraving",
    "minimal_vector",
    "gouache_editorial",
    "stained_glass",
    "mineral_pigment",
]

EXPECTED_SWATCHES = {
    "silkscreen_editorial": ("#21409A", "#F6E8C9", "#F25F5C"),
    "paper_cut_layered": ("#63B3ED", "#F6C453", "#E34D3B"),
    "watercolor_journal": ("#4FA3B7", "#B8D8BA", "#C97B84"),
    "retro_futurism": ("#1E2A5E", "#C56E33", "#F2E9D8"),
    "clay_stopmotion": ("#F2C94C", "#5DADE2", "#E96B6B"),
    "woodcut_editorial": ("#171717", "#F3E6C8", "#B52A2A"),
    "glassmorphism_tech": ("#25304A", "#67E8F9", "#A78BFA"),
    "children_science_picturebook": ("#F5C542", "#67B76F", "#5AA7E8"),
    "architectural_blueprint": ("#165DFF", "#F8FAFC", "#FF8A34"),
    "textile_embroidery": ("#344E7A", "#F4ECD8", "#A64B3C"),
    "ink_wash_editorial": ("#1B1D1F", "#264653", "#C43D2F"),
    "art_deco_night": ("#0D3B2E", "#D4AF37", "#F5E6C8"),
    "isometric_miniature": ("#8EC5FC", "#F9C74F", "#90BE6D"),
    "pixel_arcade": ("#2B174A", "#00D4FF", "#FF4D8D"),
    "cel_animation": ("#243B6B", "#F2C14E", "#E85D75"),
    "chibi_sticker": ("#F8BBD0", "#B39DDB", "#81D4FA"),
    "pencil_storyboard": ("#4A4A4A", "#D9CBB6", "#B76E79"),
    "natural_history_engraving": ("#5B4636", "#C9B27C", "#6B7D4E"),
    "minimal_vector": ("#111827", "#F9FAFB", "#FF6B35"),
    "gouache_editorial": ("#D95D39", "#E9C46A", "#2A9D8F"),
    "stained_glass": ("#2E1A47", "#1F7A8C", "#C99700"),
    "mineral_pigment": ("#B33A3A", "#235789", "#C6A15B"),
}


def test_public_catalog_has_two_modes_and_22_stable_presets():
    validate_style_catalog()
    options = public_image_theme_options()
    assert [item["key"] for item in options[:2]] == ["random_preset", "custom"]
    assert all(item["kind"] == "mode" for item in options[:2])
    assert [item["key"] for item in options[2:]] == EXPECTED_PRESET_KEYS
    assert all(item["kind"] == "preset" for item in options[2:])
    assert len(options) == 24
    assert options[0]["variation_count"] == 352


def test_every_family_has_16_variations_valid_swatches_and_safe_visual_language():
    forbidden = (
        "分栏", "卡片", "数据面板", "跨格",
        "REFERENCE_0", "参考图", "艺术家", "品牌", "角色 IP",
    )
    for family in STYLE_FAMILIES:
        assert family.variation_count == 16
        assert len(family.swatches) == 3
        assert family.swatches == EXPECTED_SWATCHES[family.key]
        assert all(re.fullmatch(r"#[0-9A-F]{6}", color) for color in family.swatches)
        visual_text = " ".join((
            family.description,
            *family.media,
            *family.palette,
            *family.texture,
            *family.light,
        ))
        assert all(term not in visual_text for term in forbidden), family.key


def test_same_group_date_and_named_family_are_reproducible():
    first = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    second = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    assert second.style_seed == first.style_seed
    assert second.style_signature == first.style_signature
    assert second.prompt == first.prompt

    named = resolve_image_theme("ink_wash_editorial", group_key="group-1", run_date="2026-08-21")
    named_again = resolve_image_theme("ink_wash_editorial", group_key="group-1", run_date="2026-08-21")
    assert named.actual_key == "ink_wash_editorial"
    assert named.style_signature == named_again.style_signature
    assert STYLE_SAFETY_SUFFIX in named.prompt


def test_random_style_only_controls_art_direction_not_panel_geometry():
    forbidden_layout_phrases = (
        "版式使用",
        "卡片",
        "数据面板",
        "分栏",
        "路线式阅读",
        "信息节点",
        "中心主视觉",
        "跨格",
    )
    for family in STYLE_FAMILIES:
        resolved = resolve_image_theme(family.key, group_key="group-1", run_date="2026-08-21")
        assert all(phrase not in resolved.prompt for phrase in forbidden_layout_phrases), family.key
        assert "配色为" in resolved.prompt
        assert "光影为" in resolved.prompt


def test_next_date_excludes_previous_signature_and_keeps_named_family():
    first = resolve_image_theme("watercolor_journal", group_key="group-1", run_date="2026-08-21")
    next_day = resolve_image_theme(
        "watercolor_journal",
        group_key="group-1",
        run_date="2026-08-22",
        previous_signature=first.style_signature,
    )
    other_group = resolve_image_theme("watercolor_journal", group_key="group-2", run_date="2026-08-21")
    assert next_day.actual_key == first.actual_key == "watercolor_journal"
    assert next_day.style_signature != first.style_signature
    assert other_group.style_seed != first.style_seed


def test_current_and_safe_v2_persisted_styles_are_reused_verbatim():
    first = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    restored = resolve_image_theme(
        "random_preset",
        group_key="changed-key",
        run_date="2026-08-30",
        persisted_meta=first.to_meta(),
    )
    assert restored.style_signature == first.style_signature
    assert restored.style_seed == first.style_seed
    assert restored.prompt == first.prompt

    v2_meta = {
        **first.to_meta(),
        "resolved_theme": "daily_random",
        "style_catalog_version": "daily-style-v2",
        "theme_prompt": "统一采用颗粒丝网印刷形式；配色为群青与奶油白；加入纸张颗粒；光影为平面高对比光影。",
    }
    restored_v2 = resolve_image_theme("random_preset", persisted_meta=v2_meta)
    assert restored_v2.prompt == v2_meta["theme_prompt"]
    assert restored_v2.catalog_version == "daily-style-v2"


def test_polluted_v1_style_is_not_restored():
    current = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-24")
    legacy_meta = {
        **current.to_meta(),
        "style_catalog_version": "daily-style-v1",
        "theme_prompt": "旧风格；版式使用纵向数据面板与浮层卡片。",
    }
    rebuilt = resolve_image_theme(
        "random_preset",
        group_key="group-1",
        run_date="2026-08-24",
        persisted_meta=legacy_meta,
    )
    assert rebuilt.catalog_version == STYLE_CATALOG_VERSION
    assert "数据面板" not in rebuilt.prompt
    assert "卡片" not in rebuilt.prompt


def test_custom_and_legacy_concrete_themes_remain_compatible():
    custom = resolve_image_theme("custom", "低饱和黏土摄影", group_key="group-1", run_date="2026-08-21")
    assert "低饱和黏土摄影" in custom.prompt
    assert "统一采用" not in custom.prompt
    for key in ("blue_white", "ultraman", "pink", "bull"):
        assert resolve_image_theme(key).actual_key == key
