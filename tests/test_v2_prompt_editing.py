from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.image_themes import resolve_image_theme
from app.ai.prompt_editing import prompt_revision, replace_theme_section
from app.api.v2_ui import RunPromptUpdateBody, restore_run_prompt, update_run_prompt
from app.v2.run_store import RunStore


def test_theme_replacement_preserves_facts_and_other_manual_content():
    original = """【大主题】
旧主题

【事件】
小王发布 3 个版本，金额 128 元。

【手工补充】
这句话必须保留。
"""
    updated = replace_theme_section(original, resolve_image_theme("pink"))

    assert updated.count("【大主题】") == 1
    assert "粉红色" in updated
    assert "小王发布 3 个版本，金额 128 元。" in updated
    assert "这句话必须保留。" in updated
    assert "旧主题" not in updated


def test_theme_is_inserted_when_prompt_has_no_canonical_section():
    updated = replace_theme_section("【事件】\n真实事件\n", resolve_image_theme("guochao"))
    assert updated.startswith("【大主题】\n国潮")
    assert "【事件】\n真实事件" in updated


def test_random_theme_resolves_to_one_fixed_concrete_theme():
    class Picker:
        @staticmethod
        def choice(_):
            return "cyber_neon"

    resolved = resolve_image_theme("random_preset", rng=Picker())
    assert resolved.requested_key == "random_preset"
    assert resolved.actual_key == "cyber_neon"
    prompt = replace_theme_section("【事件】\n不变", resolved)
    assert "赛博霓虹" in prompt
    assert "预设随机" not in prompt


def test_run_prompt_backup_revision_conflict_and_restore(tmp_path):
    settings = SimpleNamespace(output_dir=tmp_path)
    store = RunStore(tmp_path)
    group = "Prompt 测试群"
    run_date = "2026-08-21"
    original = "【大主题】\n旧主题\n\n【事件】\n原始事实\n"
    store.save_run(group, run_date, {"status": "SENT", "image_theme": "blue_white"})
    store.prompt_path(group, run_date).write_text(original, encoding="utf-8")

    saved = update_run_prompt(
        group,
        run_date,
        RunPromptUpdateBody(
            content="【大主题】\n旧主题\n\n【事件】\n修正后的真实事实\n",
            expected_revision=prompt_revision(original),
            image_theme="pink",
        ),
        settings,
    )
    assert "修正后的真实事实" in saved["content"]
    assert store.original_prompt_path(group, run_date).read_text(encoding="utf-8") == original
    assert store.load_run(group, run_date)["send_hold"] is True

    with pytest.raises(HTTPException) as conflict:
        update_run_prompt(
            group,
            run_date,
            RunPromptUpdateBody(
                content="过期页面写入",
                expected_revision=prompt_revision(original),
                image_theme="pink",
            ),
            settings,
        )
    assert conflict.value.status_code == 409

    restored = restore_run_prompt(group, run_date, settings)
    assert restored["content"] == original
    assert store.original_prompt_path(group, run_date).read_text(encoding="utf-8") == original
