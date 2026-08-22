"""每日随机画风的确定性、换日排除和自定义覆盖。"""

from app.ai.image_themes import public_image_theme_options, resolve_image_theme


def test_same_group_and_date_is_reproducible():
    first = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    second = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    assert second.style_seed == first.style_seed
    assert second.style_signature == first.style_signature
    assert second.prompt == first.prompt


def test_next_date_excludes_previous_style_and_groups_have_independent_seeds():
    first = resolve_image_theme("random_preset", group_key="group-1", run_date="2026-08-21")
    next_day = resolve_image_theme(
        "random_preset",
        group_key="group-1",
        run_date="2026-08-22",
        previous_signature=first.style_signature,
    )
    other_group = resolve_image_theme("random_preset", group_key="group-2", run_date="2026-08-21")
    assert next_day.style_signature != first.style_signature
    assert other_group.style_seed != first.style_seed


def test_force_rerun_reuses_persisted_style():
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


def test_custom_fully_replaces_random_and_ui_only_has_two_modes():
    custom = resolve_image_theme("custom", "低饱和黏土摄影", group_key="group-1", run_date="2026-08-21")
    assert "低饱和黏土摄影" in custom.prompt
    assert "统一采用" not in custom.prompt
    assert [item["key"] for item in public_image_theme_options()] == ["random_preset", "custom"]
