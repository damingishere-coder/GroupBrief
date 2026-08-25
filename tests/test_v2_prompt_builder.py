"""V2 P4：ImagePromptBuilder 单元测试。

通过注入 FakeDeepSeek（实现 _chat 契约）隔离真实 API，
验证：模板渲染 / 输入组装 / 分块策略 / 元数据 / 失败路径。
"""

from __future__ import annotations

from datetime import datetime
import json
import re

import pytest

from app.ai.prompt_builder import DeepSeekImagePromptBuilder, build_grounded_story_material
from app.ai.prompt_builder_types import PromptInput
from app.ai.image_themes import STYLE_FAMILY_KEYS
from app.ai.prompt_templates import (
    DEFAULT_IMAGE_PROMPT_TEMPLATE,
    ImagePromptTemplateError,
    ImagePromptTemplateService,
)
from app.config.settings import PROJECT_ROOT
from app.data_sources.base import V2Message


def _msg(sender: str, content: str = "hi", mtype: str = "text", i: int = 0) -> V2Message:
    return V2Message(
        message_id=f"m{i}",
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id=f"wxid_{sender}",
        sender_name=sender,
        timestamp=datetime(2026, 8, 17, 10, 30, 0),
        message_type=mtype,
        content=content,
    )


def _input(
    messages=None,
    template="default",
    image_theme="blue_white",
    image_theme_custom="",
    recent_layout_history=(),
    persisted_theme_meta=None,
) -> PromptInput:
    return PromptInput(
        group_name="示例交流群 A",
        group_id="group-1",
        run_date="2026-08-18",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        message_count=3,
        speaker_count=2,
        messages=messages or [_msg("张三", "今天群里聊了票房", i=1), _msg("李四", "《牛来》破500万了", i=2)],
        template=template,
        image_theme=image_theme,
        image_theme_custom=image_theme_custom,
        recent_layout_history=tuple(recent_layout_history),
        persisted_theme_meta=persisted_theme_meta,
    )


class FakeDeepSeek:
    """模拟 DeepSeek 底层 _chat。"""

    def __init__(self, model="deepseek-v4-flash", raise_on: Exception | None = None):
        self.model = model
        self.calls: list[tuple[str, str]] = []
        self.raise_on = raise_on

    def _chat(self, messages: list[dict], **kwargs) -> str:
        if self.raise_on:
            raise self.raise_on
        system = messages[0]["content"]
        user = messages[1]["content"]
        self.calls.append((system, user))
        if kwargs.get("response_format") == "json_object":
            matches = list(dict.fromkeys(re.findall(r"消息ID:([^\]#]+)(?:#片段\d+/\d+)?\]", user)))
            if "可选分镜骨架" in user and "已入选主题" in user:
                topic_ids = list(dict.fromkeys(re.findall(r'"topic_id":"(topic-[^"]+)"', user)))
                history_text = user.split("最近分镜历史：", 1)[-1].split("\n可选分镜骨架：", 1)[0]
                layout_id = "cinematic_strips" if '"layout_id":"hero_with_insets"' in history_text else "hero_with_insets"
                return json.dumps(
                    {
                        "layout_id": layout_id,
                        "structure_mode": "dual_rhythm" if len(topic_ids) == 2 else "hero_rhythm",
                        "featured_topic_ids": topic_ids[:2] if len(topic_ids) == 2 else topic_ids[:1],
                        "topic_order": topic_ids,
                        "panel_beats": [
                            {
                                "topic_id": topic_id,
                                "shots": ["establishing", "punchline"] if index == 0 else ["dialogue"],
                            }
                            for index, topic_id in enumerate(topic_ids)
                        ],
                        "comedy_device": "反差",
                        "layout_reason": "用大小格和连续镜头呈现真实对话",
                    },
                    ensure_ascii=False,
                )
            if '"candidates"' in user:
                ids = matches or re.findall(r'"message_ids":\["([^"]+)', user) or ["m1", "m2"]
                if len(ids) == 1:
                    ids = [ids[0], ids[0]]
                return json.dumps({
                    "candidates": [
                        {
                            "topic_id": "topic-01",
                            "title": "真实事件发起",
                            "summary": "聊天中真实发生的事件发起",
                            "people": ["张三"],
                            "quotes": ["真实原话"],
                            "start_time": "2026-08-17 10:30",
                            "end_time": "2026-08-17 10:30",
                            "message_ids": [ids[0]],
                            "comedy_score": 35,
                            "group_recognition_score": 18,
                            "visual_score": 18,
                            "comedy_angle": "真实原话形成反差",
                            "visual_gag": "把原话字面化",
                            "score_reason": "讨论有趣且适合画面",
                        },
                        {
                            "topic_id": "topic-02",
                            "title": "真实事件回应",
                            "summary": "聊天中真实发生的群友回应",
                            "people": ["李四"],
                            "quotes": ["回应原话"],
                            "start_time": "2026-08-17 10:30",
                            "end_time": "2026-08-17 10:30",
                            "message_ids": [ids[1]],
                            "comedy_score": 32,
                            "group_recognition_score": 16,
                            "visual_score": 16,
                            "comedy_angle": "群友回应形成回环",
                            "visual_gag": "用回应做成回环气泡",
                            "score_reason": "多人回应",
                        },
                    ]
                }, ensure_ascii=False)
            match = matches[0] if matches else "m1"
            return json.dumps({
                "events": [{
                    "title": "真实事件",
                    "people": ["张三"],
                    "content": "聊天中真实发生的连续事件",
                    "quotes": ["真实原话"],
                    "start_time": "2026-08-17 10:30",
                    "end_time": "2026-08-17 10:30",
                    "message_ids": [match],
                }]
            }, ensure_ascii=False)
        # 模拟：返回满足固定头尾合同的最终 Prompt；故事、主题和分镜由构建器补齐。
        return """生成成功
【群名称】
示例交流群 A

【固定画面日期】
统计日期：2026-08-17

【统计时间】
2026-08-17 00:00:00 ~ 2026-08-17 23:59:59

【数据】
3 条消息
2 人发言

【主标题】
票房梗在群里起飞

【副标题】
张三和李四接住真实对话

【底部总结】
两句真话把今天聊成了漫画
"""


class TruncatedOnceDeepSeek(FakeDeepSeek):
    """第一次候选 JSON 截断，第二次返回完整响应。"""

    def __init__(self):
        super().__init__()
        self.structured_calls = 0

    def _chat(self, messages: list[dict], **kwargs) -> str:
        if kwargs.get("response_format") == "json_object":
            self.structured_calls += 1
            if self.structured_calls == 1:
                self.calls.append((messages[0]["content"], messages[1]["content"]))
                return '{"candidates":[{"topic_id":"topic-01","title":"截断'
        return super()._chat(messages, **kwargs)


def _builder(fake: FakeDeepSeek | None = None, tmp_path=None):
    return DeepSeekImagePromptBuilder(
        provider=fake or FakeDeepSeek(),
        templates=ImagePromptTemplateService(templates_dir=(tmp_path / "image_prompt") if tmp_path else None),
    )


def test_build_success_returns_prompt():
    b = _builder()
    out = b.build(_input())
    assert out.success is True
    assert "生成成功" in out.prompt
    assert out.model == "deepseek-v4-flash"
    assert out.meta is not None
    assert out.meta["mode"] == "direct"
    assert out.meta["api_model"] == "deepseek-v4-flash"
    assert "template" in out.meta
    assert out.meta["topic_selection"]["selected_count"] == 2
    assert out.meta["layout_id"] == "hero_with_insets"
    assert out.meta["structure_mode"] == "dual_rhythm"
    assert out.meta["featured_topic_ids"] == ["topic-01", "topic-02"]
    assert out.meta["topic_order"] == ["topic-01", "topic-02"]
    assert out.meta["panel_count"] == 3
    assert out.meta["comedy_device"] == "反差"
    assert "统计日期：2026-08-17" in out.prompt
    assert "今天群里聊了票房" in out.prompt
    assert "《牛来》破500万了" in out.prompt
    assert "张三" in out.prompt
    assert "李四" in out.prompt
    assert "参与群友" not in out.prompt
    assert "事实信息" not in out.prompt
    assert "真实原话" not in out.prompt
    assert "信息卡" not in out.prompt
    assert "topic-" not in out.prompt


def test_direct_candidate_truncation_retries_once_and_records_real_call_count():
    fake = TruncatedOnceDeepSeek()
    out = _builder(fake=fake).build(_input())

    assert out.success is True
    assert fake.structured_calls == 3
    assert out.meta["api_call_count"] == 4
    assert "上一次响应不完整" in fake.calls[1][1]


def test_build_input_contains_group_and_data():
    b = _builder()
    out = b.build(_input())
    assert out.success
    system, _ = b._provider.calls[-1]
    _, user = b._provider.calls[0]
    assert "示例交流群 A" in system  # 模板变量已渲染进输出结构
    assert "3 条消息" in system  # 数据渲染进输出结构
    assert "2 人发言" in system
    assert "完整群聊记录" in user  # 候选提取输入含完整聊天内容
    assert "张三" in user


def test_build_media_type_prefix():
    messages = [
        _msg("张三", "IMAGE_BINARY_BODY", mtype="image", i=1),
        _msg("李四", "FILE_BINARY_BODY", mtype="file", i=2),
        _msg("王五", "", mtype="voice", i=3),
    ]
    b = _builder()
    out = b.build(_input(messages=messages))
    assert out.success
    _, user = b._provider.calls[0]
    assert "[图片]" in user
    assert "[文件]" in user
    assert "[语音]" in user
    assert "IMAGE_BINARY_BODY" not in user
    assert "FILE_BINARY_BODY" not in user


def test_chunk_strategy_for_long_chat():
    # 构造超过字符预算的连续消息，验证自然分段、结构化提取与最终合并。
    messages = [_msg(f"用户{n}", f"消息{n}-" + ("连续内容" * 40), i=n) for n in range(150)]
    b = _builder()
    b.settings.max_context_chars = 1_000
    out = b.build(_input(messages=messages))
    assert out.success
    assert out.meta["mode"] == "natural_chunked"
    assert out.meta["chunk_count"] > 1
    assert len(b._provider.calls) == out.meta["chunk_count"] + 3
    assert "message_ids" in b._provider.calls[0][1]


def test_build_failure_returns_error():
    b = _builder(fake=FakeDeepSeek(raise_on=RuntimeError("DeepSeek 挂了")))
    out = b.build(_input())
    assert out.success is False
    assert "DeepSeek 挂了" in out.error
    assert out.model == "deepseek-v4-flash"


def test_unknown_template_returns_error(tmp_path):
    b = _builder(tmp_path=tmp_path)
    out = b.build(_input(template="not_exist"))
    assert out.success is False
    assert "不存在" in out.error


def test_template_variable_render():
    from app.ai.prompt_templates import render_image_prompt_template

    text = render_image_prompt_template(
        "【群名称】{{group_name}}\n【数据】{{message_count}}条/{{speaker_count}}人\n【时间】{{period_start}}~{{period_end}}",
        {"group_name": "示例群", "message_count": "409", "speaker_count": "27",
         "period_start": "2026-08-17 00:00:00", "period_end": "2026-08-17 23:59:59"},
    )
    assert "【群名称】示例群" in text
    assert "【数据】409条/27人" in text
    assert "2026-08-17 00:00:00~2026-08-17 23:59:59" in text

    preview = render_image_prompt_template("{{layout_name}}\n{{layout_instruction}}", {})
    assert "{{layout_name}}" not in preview
    assert "生成时自动选择漫画分镜骨架" in preview


def test_default_template_reset_contains_theme_and_safe_topic_limit(tmp_path):
    service = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    service.save("default", "{{group_name}}")
    restored = service.reset()
    assert restored == DEFAULT_IMAGE_PROMPT_TEMPLATE
    assert "{{image_theme}}" in restored
    assert "{{layout_name}}" in restored
    assert "{{layout_instruction}}" in restored
    assert "2～7 个入选主题" in restored
    assert "统计日期：{{report_date}}" in restored
    assert "顶部大标题，中部按事件分区" not in restored
    assert "整齐两列等高矩形" in restored


def test_concrete_theme_enters_prompt_and_metadata():
    b = _builder()
    out = b.build(_input(image_theme="pink"))
    assert out.success
    assert out.meta["requested_theme"] == "pink"
    assert out.meta["resolved_theme"] == "pink"
    assert out.meta["theme_display_name"] == "粉红色"
    assert "粉红" in b._provider.calls[-1][0]


def test_all_concrete_themes_are_supported():
    from app.ai.image_themes import CONCRETE_THEME_KEYS

    for key in CONCRETE_THEME_KEYS:
        b = _builder()
        out = b.build(_input(image_theme=key))
        assert out.success, key
        assert out.meta["resolved_theme"] == key


def test_all_public_style_families_build_with_fixed_family_keys():
    for key in STYLE_FAMILY_KEYS:
        out = _builder().build(_input(image_theme=key))
        assert out.success, key
        assert out.meta["requested_theme"] == key
        assert out.meta["resolved_theme"] == key
        assert out.meta["style_catalog_version"] == "daily-style-v3"


def test_random_theme_records_daily_reproducible_theme():
    b = _builder()
    out = b.build(_input(image_theme="random_preset"))
    assert out.success
    assert out.meta["requested_theme"] == "random_preset"
    assert out.meta["resolved_theme"] in STYLE_FAMILY_KEYS
    assert out.meta["style_signature"]
    assert out.meta["style_seed"]
    second = _builder().build(_input(image_theme="random_preset"))
    assert second.meta["style_signature"] == out.meta["style_signature"]


def test_default_file_and_builtin_prompt_contract_are_synchronized():
    file_text = (PROJECT_ROOT / "templates" / "image_prompt" / "default.md").read_text(encoding="utf-8")
    file_body = re.sub(r"<!--.*?-->", "", file_text, flags=re.DOTALL).strip()
    assert file_body == DEFAULT_IMAGE_PROMPT_TEMPLATE.strip()
    for required in (
        "【使用场景与画布】",
        "1024×1536",
        "【逐话题可见文字合同】",
        "不超过 12 个汉字",
        "不超过 24 个汉字",
        "不超过 22 个汉字",
        "景别 + 人物动作 + 群友反应或道具特写 + 逐字气泡",
        "装饰 → 次要气泡 → 次要反应细节",
        "群名称、完整统计时段、主标题、副标题、底部总结",
        "【重新生图不变量】",
        "逐字且恰好出现一次",
    ):
        assert required in DEFAULT_IMAGE_PROMPT_TEMPLATE


def test_grounded_story_material_enforces_visible_text_budgets_and_shot_contract():
    material = build_grounded_story_material(
        {
            "candidates": [{
                "topic_id": "topic-01",
                "selected": True,
                "title": "这是一个明显超过十二个汉字的话题标题",
                "summary": "这是一句明显超过二十四个汉字而且仍然继续延伸的真实事实摘要",
                "visible_participants": ["张三完整昵称"],
                "quotes": ["这是一句明显超过二十二个汉字而且仍然继续延伸的真实原话"],
            }],
        },
        ("topic-01",),
    )
    title = re.search(r"短标题逐字写《([^》]+)》", material).group(1)
    fact = re.search(r"事实短句逐字写“([^”]+)”", material).group(1)
    quote = re.search(r"主气泡逐字写“([^”]+)”", material).group(1)
    assert len(title) <= 12
    assert len(fact) <= 24
    assert len(quote) <= 22
    assert "完整姓名逐字写“张三完整昵称”" in material
    assert "景别、该人物的具体动作" in material
    assert "恰好出现一次" in material


def test_custom_theme_is_injected_but_ai_free_has_no_style_intervention():
    b = _builder()
    custom = b.build(_input(image_theme="custom", image_theme_custom="夏日海边漫画"))
    assert custom.success
    assert custom.meta["requested_theme"] == "custom"
    assert custom.meta["resolved_theme"] == "custom"
    assert custom.meta["theme_display_name"] == "夏日海边漫画"
    assert "夏日海边漫画" in b._provider.calls[-1][0]

    b2 = _builder()
    ai_free = b2.build(_input(image_theme="ai_free"))
    assert ai_free.success
    final_system = b2._provider.calls[-1][0]
    assert "根据当天真实聊天内容自由选择统一视觉风格。" in final_system
    assert "大主题约束｜全图最高视觉约束" not in final_system
    assert ai_free.meta["style_intervention"] is False
    assert ai_free.prompt.count("【视觉风格】") == 1
    assert ai_free.prompt.count("根据当天真实聊天内容自由选择统一视觉风格。") == 1
    assert "【大主题】" not in ai_free.prompt


def test_persisted_topic_selection_and_storyboard_skip_analysis_calls():
    first = _builder().build(_input(image_theme="ai_free"))
    assert first.success

    fake = FakeDeepSeek()
    data = _input(image_theme="ai_free", persisted_theme_meta=first.meta)
    data.persisted_topic_selection = first.meta["topic_selection"]
    rebuilt = _builder(fake=fake).build(data)

    assert rebuilt.success
    assert rebuilt.meta["mode"] == "persisted_topic_selection"
    assert rebuilt.meta["topic_selection_reused"] is True
    assert rebuilt.meta["layout_reused"] is True
    assert rebuilt.meta["api_call_count"] == 1
    assert len(fake.calls) == 1
    assert "大主题约束｜全图最高视觉约束" not in fake.calls[0][0]


def test_final_prompt_rejects_conflicting_header_footer_instructions():
    class ConflictingFinalPrompt(FakeDeepSeek):
        def _chat(self, messages: list[dict], **kwargs) -> str:
            if kwargs.get("response_format") == "json_object":
                return super()._chat(messages, **kwargs)
            self.calls.append((messages[0]["content"], messages[1]["content"]))
            return """【群名称】
示例交流群 A，仅作背景识别，不作为画面文字绘制。
【统计时间】
2026-08-17 00:00:00 ~ 2026-08-17 23:59:59
【数据】
3 条消息
2 人发言
【主标题】
真实标题
【副标题】
不设置、不绘制
【底部总结】
不绘制，避免增加文字
"""

    out = _builder(fake=ConflictingFinalPrompt()).build(_input(image_theme="ai_free"))
    assert out.success is False
    assert "固定头尾合同" in out.error


def test_invalid_custom_theme_fails_before_model_call():
    b = _builder()
    out = b.build(_input(image_theme="custom", image_theme_custom=""))
    assert out.success is False
    assert "1～80" in out.error
    assert b._provider.calls == []


def test_legacy_template_still_gets_theme_system_constraint(tmp_path):
    templates = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    templates.save("legacy", "【群名称】{{group_name}}\n【版面】最多五个话题")
    b = DeepSeekImagePromptBuilder(provider=FakeDeepSeek(), templates=templates)
    out = b.build(_input(template="legacy", image_theme="cyber_neon"))
    assert out.success
    assert "赛博霓虹" in b._provider.calls[-1][0]
    assert "2～7" in b._provider.calls[-1][0]
    assert "漫画分镜约束" in b._provider.calls[-1][0]
    assert "今天群里聊了票房" in out.prompt
    assert "参与群友" not in out.prompt


def test_group_template_override_cannot_remove_grounded_story_material(tmp_path):
    b = _builder(tmp_path=tmp_path)
    data = _input()
    data.template_override = "【群名称】{{group_name}}\n只画装饰"
    out = b.build(data)
    assert out.success
    assert "张三" in out.prompt
    assert "李四" in out.prompt
    assert "今天群里聊了票房" in out.prompt
    assert "《牛来》破500万了" in out.prompt
    assert "参与群友" not in out.prompt
    assert "事实信息" not in out.prompt
    final_system = b._provider.calls[-1][0]
    assert "不得只画匿名人物或自由生成人名" in final_system


def test_previous_layout_is_avoided_and_theme_remains_independent_hard_constraint():
    b = _builder()
    out = b.build(
        _input(
            image_theme="custom",
            image_theme_custom="低饱和黏土摄影",
            recent_layout_history=({"layout_id": "hero_with_insets", "comedy_device": "字面化"},),
        )
    )
    assert out.success
    assert out.meta["layout_id"] == "cinematic_strips"
    assert out.meta["recent_layout_ids"] == ["hero_with_insets"]
    assert "低饱和黏土摄影" in out.prompt
    final_system = b._provider.calls[-1][0]
    assert "大主题约束｜全图最高视觉约束" in final_system
    assert "漫画分镜只控制格子几何" in final_system


def test_legacy_same_date_layout_is_not_reused_for_new_prompt():
    legacy = {
        "layout_id": "group_court",
        "hero_topic_id": "topic-02",
        "support_topic_ids": ["topic-01"],
        "comedy_device": "一本正经地荒诞",
        "layout_reason": "同日已有选择",
    }
    b = _builder()
    out = b.build(_input(persisted_theme_meta=legacy))
    assert out.success
    assert out.meta["layout_catalog_version"] == "comic-panels-v3"
    assert out.meta["layout_reused"] is False
    assert out.meta["api_call_count"] == 3  # 候选 + 新版式 + 最终 Prompt
    assert len(b._provider.calls) == 3


def test_same_date_reuses_v3_storyboard_without_director_call():
    persisted = {
        "layout_catalog_version": "comic-panels-v3",
        "layout_id": "split_focus",
        "structure_mode": "dual_rhythm",
        "featured_topic_ids": ["topic-01", "topic-02"],
        "topic_order": ["topic-02", "topic-01"],
        "panel_beats": [
            {"topic_id": "topic-02", "shots": ["establishing", "reaction"]},
            {"topic_id": "topic-01", "shots": ["dialogue"]},
        ],
        "comedy_device": "一本正经地荒诞",
        "layout_reason": "同日已有双核心选择",
    }
    b = _builder()
    out = b.build(_input(persisted_theme_meta=persisted))
    assert out.success
    assert out.meta["layout_id"] == "split_focus"
    assert out.meta["structure_mode"] == "dual_rhythm"
    assert out.meta["layout_reused"] is True
    assert out.meta["panel_count"] == 3
    assert out.meta["api_call_count"] == 2
    assert len(b._provider.calls) == 2


def test_explicit_layout_inside_custom_style_wins_without_changing_style_text():
    custom_style = "复古报纸三栏头版"
    b = _builder()
    out = b.build(_input(image_theme="custom", image_theme_custom=custom_style))
    assert out.success
    assert out.meta["layout_id"] == "staggered_mosaic"
    assert out.meta["style_layout_locked"] is True
    assert out.meta["structure_mode"] == "dual_rhythm"
    assert custom_style in out.prompt
    assert out.meta["api_call_count"] == 2

    from app.ai.image_themes import resolve_image_theme
    from app.ai.prompt_editing import replace_theme_section

    switched = replace_theme_section(out.prompt, resolve_image_theme("pink"))
    assert custom_style not in switched
    assert "粉红色" in switched
    assert len(re.findall(r"(?m)^【大主题】$", switched)) == 1
