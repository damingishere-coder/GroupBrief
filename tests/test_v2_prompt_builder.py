"""固定群聊漫画 Prompt：证据编辑、固定结构、风格与快照复用。"""

from __future__ import annotations

from datetime import datetime
import json
import re

import pytest

from app.ai.image_themes import STYLE_FAMILY_KEYS
from app.ai.poster_copy import PosterCopyError, parse_poster_copy, validate_fixed_prompt_contract
from app.ai.prompt_builder import DeepSeekImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.ai.prompt_templates import DEFAULT_IMAGE_PROMPT_TEMPLATE, ImagePromptTemplateService
from app.config.settings import PROJECT_ROOT
from app.data_sources.base import V2Message


def _msg(sender: str, content: str, i: int) -> V2Message:
    return V2Message(
        message_id=f"m{i}",
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id=f"wxid_{sender}",
        sender_name=sender,
        timestamp=datetime(2026, 8, 17, 10, 30, i),
        message_type="text",
        content=content,
    )


def _input(**changes) -> PromptInput:
    values = {
        "group_name": "示例交流群 A",
        "visible_group_name": "示例交流群 A（实时名）",
        "group_id": "group-1",
        "run_date": "2026-08-18",
        "period_start": "2026-08-17 00:00:00",
        "period_end": "2026-08-17 23:59:59",
        "message_count": 4,
        "speaker_count": 2,
        "messages": [
            _msg("张三", "今天群里聊了票房", 1),
            _msg("李四", "《牛来》破500万了", 2),
            _msg("张三", "那就把票房画成火箭", 3),
            _msg("李四", "我在下面接着", 4),
        ],
        "template": "default",
        "image_theme": "ai_free",
        "image_theme_custom": "",
    }
    values.update(changes)
    return PromptInput(**values)


def _raw_json_from_user(user: str) -> dict:
    start = user.index('{"copy_version"')
    payload, _ = json.JSONDecoder().raw_decode(user[start:])
    return payload


class FakeSummaryProvider:
    model = "gpt-5.6-sol"

    def __init__(self, fail: Exception | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def _chat(self, messages: list[dict], **kwargs) -> str:
        if self.fail:
            raise self.fail
        system = messages[0]["content"]
        user = messages[1]["content"]
        self.calls.append((system, user))
        if '"copy_version":"fixed-chat-comic-v1"' in user:
            source = _raw_json_from_user(user)
            panels = []
            for topic in source["topics"]:
                options = topic["participant_options"]
                dialogue = topic["evidence_dialogue"]
                dialogue_by_name = {}
                for entry in dialogue:
                    dialogue_by_name.setdefault(entry["speaker"], entry["text"])
                participants = []
                for name in options[:4]:
                    participants.append({
                        "name": name,
                        "action": "站在同一张桌边接话并看向其他群友",
                        "quote": dialogue_by_name.get(name, ""),
                    })
                panels.append({
                    "topic_id": topic["topic_id"],
                    "title": topic["source_title"][:18],
                    "event_summary": topic["source_summary"],
                    "composition": "采用大小错落的群聊场景，人物围绕同一事件形成前后接话",
                    "participants": participants,
                    "visual_gag": topic["source_visual_gag"] or "用人物反应形成不改变事实的视觉反差",
                    "fact_line": topic["source_summary"][:72],
                })
            return json.dumps({
                "title": "票房火箭与群友接力",
                "subtitle": "张三抛梗，李四把真实对话接住",
                "panels": panels,
                "footer_summary": "票房变火箭，群友接话让讨论停不下来",
            }, ensure_ascii=False)

        ids = list(dict.fromkeys(re.findall(r"消息ID:([^\]#]+)(?:#片段\d+/\d+)?\]", user)))
        if "可选分镜骨架" in user and "已入选主题" in user:
            topic_ids = list(dict.fromkeys(re.findall(r'"topic_id":"(topic-[^"]+)"', user)))
            return json.dumps({
                "layout_id": "hero_with_insets",
                "structure_mode": "dual_rhythm",
                "featured_topic_ids": topic_ids[:2],
                "topic_order": topic_ids,
                "panel_beats": [
                    {"topic_id": topic_id, "shots": ["dialogue", "reaction"]}
                    for topic_id in topic_ids
                ],
                "comedy_device": "接话反差",
                "layout_reason": "用大小格呈现真实多人对话",
            }, ensure_ascii=False)
        if '"candidates"' in user:
            ids = ids or ["m1", "m2", "m3", "m4"]
            return json.dumps({"candidates": [
                {
                    "topic_id": "topic-01",
                    "title": "票房火箭",
                    "summary": "张三和李四把票房讨论接成火箭笑点。",
                    "people": ["张三", "李四"],
                    "quotes": ["今天群里聊了票房", "《牛来》破500万了"],
                    "start_time": "2026-08-17 10:30",
                    "end_time": "2026-08-17 10:30",
                    "message_ids": ids[:2],
                    "comedy_score": 35,
                    "group_recognition_score": 18,
                    "visual_score": 18,
                    "comedy_angle": "真实接话形成反差",
                    "visual_gag": "把票房走势画成火箭",
                    "score_reason": "多人连续回应",
                },
                {
                    "topic_id": "topic-02",
                    "title": "群友接力",
                    "summary": "两位群友继续围绕怎么画展开连续回应。",
                    "people": ["张三", "李四"],
                    "quotes": ["那就把票房画成火箭", "我在下面接着"],
                    "start_time": "2026-08-17 10:30",
                    "end_time": "2026-08-17 10:30",
                    "message_ids": ids[2:4] or ids[:2],
                    "comedy_score": 32,
                    "group_recognition_score": 16,
                    "visual_score": 16,
                    "comedy_angle": "群友接力形成回环",
                    "visual_gag": "两人一上一下接住火箭",
                    "score_reason": "连续对话适合漫画",
                },
            ]}, ensure_ascii=False)
        return json.dumps({"events": []}, ensure_ascii=False)


def _builder(provider: FakeSummaryProvider | None = None, tmp_path=None):
    return DeepSeekImagePromptBuilder(
        provider=provider or FakeSummaryProvider(),
        templates=ImagePromptTemplateService(
            templates_dir=(tmp_path / "image_prompt") if tmp_path else None
        ),
    )


def test_build_renders_only_fixed_sections_and_real_multi_person_dialogue():
    output = _builder().build(_input())

    assert output.success
    assert validate_fixed_prompt_contract(output.prompt, expected_panel_count=2) == 2
    headings = re.findall(r"(?m)^【([^\n】]+)】$", output.prompt)
    assert headings == [
        "任务", "群名称", "统计时间", "数据", "主标题", "副标题",
        "整体视觉", "漫画分镜", "版面1", "版面2", "文字规则", "底部总结",
    ]
    assert output.prompt.count("【漫画分镜】") == 1
    assert output.prompt.index("【整体视觉】") < output.prompt.index("【漫画分镜】")
    assert output.prompt.index("【漫画分镜】") < output.prompt.index("【版面1】")
    assert "每个话题先作为一个独立漫画框" in output.prompt
    assert "示例交流群 A（实时名）" in output.prompt
    assert "2026-08-17 00:00:00 ~ 2026-08-17 23:59:59" in output.prompt
    assert output.prompt.count("人物旁清晰标注“张三”") == 2
    assert output.prompt.count("人物旁清晰标注“李四”") == 2
    for quote in ("今天群里聊了票房", "《牛来》破500万了", "那就把票房画成火箭", "我在下面接着"):
        assert quote in output.prompt
    for hidden in ("topic-", "message_id", "participant_options", "evidence_dialogue"):
        assert hidden not in output.prompt


def test_summary_and_prompt_phases_use_independent_provider_instances():
    summary = FakeSummaryProvider()
    prompt = FakeSummaryProvider()
    summary.name = "summary-fake"
    prompt.name = "prompt-fake"
    builder = DeepSeekImagePromptBuilder(
        summary_provider=summary,
        prompt_provider=prompt,
    )

    output = builder.build(_input())

    assert output.success
    assert summary.calls
    assert prompt.calls
    assert output.meta["summary_provider_actual"] == "summary-fake"
    assert output.meta["prompt_provider_actual"] == "prompt-fake"
    assert output.meta["summary_api_call_count"] >= 1
    assert output.meta["prompt_api_call_count"] >= 1


def _long_quote_payload(quote: str) -> tuple[str, dict]:
    source = {
        "topics": [
            {
                "topic_id": "topic-long",
                "source_title": "部署复盘",
                "source_summary": "张三分享部署复盘，大家讨论发布流程。",
                "source_visual_gag": "把发布流程画成接力跑",
                "participant_options": ["张三"],
                "evidence_dialogue": [
                    {"message_id": "m-long", "speaker": "张三", "text": quote}
                ],
                "shot_hints": ["对白"],
            }
        ]
    }
    payload = {
        "title": "部署复盘",
        "subtitle": "张三分享发布流程",
        "panels": [
            {
                "topic_id": "topic-long",
                "title": "部署复盘",
                "event_summary": "张三分享部署复盘。",
                "composition": "张三站在流程图旁讲解",
                "participants": [
                    {"name": "张三", "action": "指向发布流程图", "quote": quote}
                ],
                "visual_gag": "把发布流程画成接力跑",
                "fact_line": "张三分享部署复盘。",
            }
        ],
        "footer_summary": "张三分享部署复盘",
    }
    return json.dumps(payload, ensure_ascii=False), source


def test_long_grounded_quote_is_reduced_to_complete_contiguous_sentence():
    long_quote = (
        "今天把部署流程从头到尾重新走了一遍，所有检查点都已经记录。"
        "明天会按照这份记录继续核对自动发布和回滚步骤，避免遗漏。"
    )
    raw, source = _long_quote_payload(long_quote)

    copy = parse_poster_copy(raw, source)
    quote = copy.panels[0].participants[0].quote

    assert 2 <= len(quote) <= 48
    assert quote in long_quote
    assert quote.endswith("。")


def test_long_grounded_quote_without_safe_boundary_is_rejected():
    long_quote = "这是一条完全没有任何标点因此无法安全判断语义边界的真实长消息" * 2
    raw, source = _long_quote_payload(long_quote)

    with pytest.raises(PosterCopyError, match="无法安全缩短"):
        parse_poster_copy(raw, source)


def test_ai_free_does_not_inject_daily_style_library():
    output = _builder().build(_input(image_theme="ai_free"))
    assert output.success
    assert output.meta["style_intervention"] is False
    assert output.prompt.count("根据当天真实聊天内容自由选择统一视觉风格。") == 1
    assert "原生画布比例严格为 2:3" in output.prompt
    assert "不得生成 9:19" in output.prompt
    assert "本次手动视觉风格" not in output.prompt
    assert "daily-style-v3" not in output.prompt


def test_manual_style_is_only_appended_inside_overall_visual():
    output = _builder().build(
        _input(image_theme="custom", image_theme_custom="低饱和黏土摄影")
    )
    assert output.success
    assert "本次手动视觉风格：低饱和黏土摄影" in output.prompt
    assert output.meta["style_intervention"] is True
    assert "【视觉风格】" not in output.prompt


def test_public_presets_remain_compatible():
    for key in STYLE_FAMILY_KEYS:
        output = _builder().build(_input(image_theme=key))
        assert output.success, key
        assert output.meta["resolved_theme"] == key
        assert "本次手动视觉风格：" in output.prompt


def test_persisted_topics_and_layout_only_call_editor_once():
    first = _builder().build(_input())
    assert first.success
    provider = FakeSummaryProvider()
    rebuilt_input = _input(persisted_theme_meta=first.meta)
    rebuilt_input.persisted_topic_selection = first.meta["topic_selection"]
    rebuilt = _builder(provider).build(rebuilt_input)
    assert rebuilt.success
    assert rebuilt.meta["topic_selection_reused"] is True
    assert rebuilt.meta["layout_reused"] is True
    assert rebuilt.meta["api_call_count"] == 1
    assert len(provider.calls) == 1


def test_fixed_validator_rejects_conflicting_header_footer_rule():
    output = _builder().build(_input())
    assert output.success
    conflicting = output.prompt.replace(
        "空间不足时先减少装饰、道具和次要反应",
        "不得绘制群名称；空间不足时先减少装饰、道具和次要反应",
    )
    try:
        validate_fixed_prompt_contract(conflicting, expected_panel_count=2)
    except PosterCopyError as exc:
        assert "冲突规则" in str(exc)
    else:
        raise AssertionError("冲突头尾规则必须被拒绝")


def test_default_template_file_and_builtin_are_synchronized():
    file_text = (PROJECT_ROOT / "templates" / "image_prompt" / "default.md").read_text(
        encoding="utf-8"
    )
    file_body = re.sub(r"<!--.*?-->", "", file_text, flags=re.DOTALL).strip()
    assert file_body == DEFAULT_IMAGE_PROMPT_TEMPLATE.strip()
    assert "{{main_title}}" in file_body
    assert "{{subtitle}}" in file_body
    assert "{{panels}}" in file_body
    assert "{{footer_summary}}" in file_body
    assert file_body.count("【漫画分镜】") == 1


def test_fixed_storyboard_contract_supports_two_five_and_seven_panels():
    output = _builder().build(_input())
    assert output.success
    for panel_count in (2, 5, 7):
        panels = "\n\n".join(
            f"【版面{index}】\n真实话题{index}"
            for index in range(1, panel_count + 1)
        )
        prompt = re.sub(
            r"(?ms)^【版面1】.*?(?=^【文字规则】)",
            panels + "\n\n",
            output.prompt,
        )
        assert validate_fixed_prompt_contract(
            prompt,
            expected_panel_count=panel_count,
        ) == panel_count
        assert prompt.count("【漫画分镜】") == 1
        assert prompt.index("【漫画分镜】") < prompt.index("【版面1】")


def test_fixed_storyboard_heading_is_required_in_exact_position():
    output = _builder().build(_input())
    assert output.success
    invalid = output.prompt.replace("【漫画分镜】", "【分镜说明】", 1)
    try:
        validate_fixed_prompt_contract(invalid, expected_panel_count=2)
    except PosterCopyError as exc:
        assert "区块名称、顺序" in str(exc)
    else:
        raise AssertionError("缺少固定漫画分镜区块必须被拒绝")


def test_template_rejects_missing_or_misordered_storyboard_section(tmp_path):
    service = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    missing = DEFAULT_IMAGE_PROMPT_TEMPLATE.replace("【漫画分镜】", "【分镜说明】", 1)
    storyboard_start = DEFAULT_IMAGE_PROMPT_TEMPLATE.index("【漫画分镜】")
    panels_start = DEFAULT_IMAGE_PROMPT_TEMPLATE.index("{{panels}}")
    storyboard = DEFAULT_IMAGE_PROMPT_TEMPLATE[storyboard_start:panels_start]
    without_storyboard = (
        DEFAULT_IMAGE_PROMPT_TEMPLATE[:storyboard_start]
        + DEFAULT_IMAGE_PROMPT_TEMPLATE[panels_start:]
    )
    misordered = without_storyboard.replace("【整体视觉】", storyboard + "【整体视觉】", 1)

    for name, content in (("missing", missing), ("misordered", misordered)):
        try:
            service.save(name, content)
        except Exception as exc:
            assert "模板区块必须严格为" in str(exc)
        else:
            raise AssertionError(f"{name} 漫画分镜模板必须被拒绝")


def test_default_reset_restores_fixed_storyboard_section(tmp_path):
    service = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    changed = DEFAULT_IMAGE_PROMPT_TEMPLATE.replace(
        "漫画分镜负责强化真实聊天中的动作、误会、吐槽、反差和群友反应",
        "临时修改的漫画分镜规则",
    )
    service.save("default", changed)

    restored = service.reset()

    assert restored == DEFAULT_IMAGE_PROMPT_TEMPLATE
    assert restored.count("【漫画分镜】") == 1
    assert service.read("default") == DEFAULT_IMAGE_PROMPT_TEMPLATE


def test_invalid_legacy_template_is_rejected_instead_of_exposing_extra_sections(tmp_path):
    service = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    try:
        service.save("legacy", "【群名称】{{group_name}}\n只画装饰")
    except Exception as exc:
        assert "模板区块必须严格为" in str(exc)
    else:
        raise AssertionError("旧自由结构模板必须被拒绝")


def test_provider_failure_returns_error_without_partial_prompt():
    output = _builder(FakeSummaryProvider(RuntimeError("总结模型不可用"))).build(_input())
    assert output.success is False
    assert "总结模型不可用" in output.error
    assert output.prompt == ""
