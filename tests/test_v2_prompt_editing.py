from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.image_themes import resolve_image_theme
from app.ai.prompt_editing import prompt_revision, replace_theme_section
from app.api.v2_ui import RunPromptUpdateBody, restore_run_prompt, update_run_prompt
from app.v2.run_store import RunStore


def _fixed_prompt(fact: str) -> str:
    return f"""【任务】
生成一张竖版微信群日报漫画信息图。
【群名称】
Prompt 测试群
【统计时间】
2026-08-20 00:00:00 ~ 2026-08-20 23:59:59
【数据】
10 条消息
2 人发言
【主标题】
真实主标题
【副标题】
真实副标题
【整体视觉】
固定群聊漫画要求。
根据当天真实聊天内容自由选择统一视觉风格。
【漫画分镜】
每个话题先作为一个独立漫画框，框内可拆成1～3个连续分镜。
【版面1】
{fact}
【版面2】
第二个真实话题
【文字规则】
保留顶部、底部、姓名和主要对白。
【底部总结】
真实底部总结
"""


def test_theme_replacement_preserves_facts_and_other_manual_content():
    original = _fixed_prompt("小王发布 3 个版本，金额 128 元。这句话必须保留。")
    updated = replace_theme_section(
        original,
        resolve_image_theme("ink_wash_editorial", group_key="group-1", run_date="2026-08-24"),
    )

    assert updated.count("【整体视觉】") == 1
    assert updated.count("【漫画分镜】") == 1
    assert "每个话题先作为一个独立漫画框" in updated
    assert "水墨留白漫画" in updated
    assert "小王发布 3 个版本，金额 128 元。" in updated
    assert "这句话必须保留。" in updated


def test_theme_is_inserted_when_prompt_has_no_canonical_section():
    updated = replace_theme_section("【事件】\n真实事件\n", resolve_image_theme("guochao"))
    assert updated.startswith("【大主题】\n国潮")
    assert "【事件】\n真实事件" in updated


def test_ai_free_replaces_concrete_theme_with_one_neutral_hint():
    original = "【大主题】\n赛博霓虹：深蓝黑底、青紫粉霓虹。\n\n【事件】\n真实事件\n"
    updated = replace_theme_section(original, resolve_image_theme("ai_free"))

    assert updated.count("【视觉风格】") == 1
    assert "根据当天真实聊天内容自由选择统一视觉风格。" in updated
    assert "赛博霓虹" not in updated
    assert "深蓝黑底" not in updated
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
    original = _fixed_prompt("原始事实")
    store.save_run(group, run_date, {"status": "SENT", "image_theme": "blue_white"})
    store.prompt_path(group, run_date).write_text(original, encoding="utf-8")

    saved = update_run_prompt(
        group,
        run_date,
        RunPromptUpdateBody(
            content=_fixed_prompt("修正后的真实事实"),
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
