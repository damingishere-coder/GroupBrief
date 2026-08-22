"""V2 P4：ImagePromptBuilder 单元测试。

通过注入 FakeDeepSeek（实现 _chat 契约）隔离真实 API，
验证：模板渲染 / 输入组装 / 分块策略 / 元数据 / 失败路径。
"""

from __future__ import annotations

from datetime import datetime
import json
import re

import pytest

from app.ai.prompt_builder import DeepSeekImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.ai.prompt_templates import (
    DEFAULT_IMAGE_PROMPT_TEMPLATE,
    ImagePromptTemplateError,
    ImagePromptTemplateService,
)
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
        group_name="茶馆V3.0（三周年纪念）",
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
            if "可选版式" in user and "已入选主题" in user:
                topic_ids = list(dict.fromkeys(re.findall(r'"topic_id":"([^"]+)"', user)))
                history_text = user.split("最近版式历史：", 1)[-1].split("\n可选版式：", 1)[0]
                layout_id = "comic_strip" if '"layout_id":"hero_cover"' in history_text else "hero_cover"
                return json.dumps(
                    {
                        "layout_id": layout_id,
                        "hero_topic_id": topic_ids[0],
                        "support_topic_ids": topic_ids[1:],
                        "comedy_device": "反差",
                        "layout_reason": "主事件突出且适合视觉反差",
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
        # 模拟：按输出结构返回
        structure_block = system.rsplit("【输出结构】", 1)[-1]
        return f"生成成功\n{structure_block[:60]}"


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
    assert out.meta["layout_id"] == "hero_cover"
    assert out.meta["hero_topic_id"] in {"topic-01", "topic-02"}
    assert out.meta["comedy_device"] == "反差"
    assert "统计日期：2026-08-17" in out.prompt


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
    assert "茶馆V3.0（三周年纪念）" in system  # 模板变量已渲染进输出结构
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
        {"group_name": "茶馆", "message_count": "409", "speaker_count": "27",
         "period_start": "2026-08-17 00:00:00", "period_end": "2026-08-17 23:59:59"},
    )
    assert "【群名称】茶馆" in text
    assert "【数据】409条/27人" in text
    assert "2026-08-17 00:00:00~2026-08-17 23:59:59" in text

    preview = render_image_prompt_template("{{layout_name}}\n{{layout_instruction}}", {})
    assert "{{layout_name}}" not in preview
    assert "生成时自动选择整张海报版式" in preview


def test_default_template_reset_contains_theme_and_safe_topic_limit(tmp_path):
    service = ImagePromptTemplateService(templates_dir=tmp_path / "image_prompt")
    service.save("default", "{{group_name}}")
    restored = service.reset()
    assert restored == DEFAULT_IMAGE_PROMPT_TEMPLATE
    assert "{{image_theme}}" in restored
    assert "{{layout_name}}" in restored
    assert "{{layout_instruction}}" in restored
    assert "2~5 个入选主题" in restored
    assert "统计日期：{{report_date}}" in restored
    assert "顶部大标题，中部按事件分区" not in restored


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


def test_random_theme_records_daily_reproducible_theme():
    b = _builder()
    out = b.build(_input(image_theme="random_preset"))
    assert out.success
    assert out.meta["requested_theme"] == "random_preset"
    assert out.meta["resolved_theme"] == "daily_random"
    assert out.meta["style_signature"]
    assert out.meta["style_seed"]
    second = _builder().build(_input(image_theme="random_preset"))
    assert second.meta["style_signature"] == out.meta["style_signature"]


def test_custom_theme_and_ai_free_theme_are_explicitly_injected():
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
    assert "统一视觉主题" in b2._provider.calls[-1][0]


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
    assert "2～5" in b._provider.calls[-1][0]
    assert "整体版式约束" in b._provider.calls[-1][0]


def test_previous_layout_is_avoided_and_theme_remains_independent_hard_constraint():
    b = _builder()
    out = b.build(
        _input(
            image_theme="custom",
            image_theme_custom="低饱和黏土摄影",
            recent_layout_history=({"layout_id": "hero_cover", "comedy_device": "字面化"},),
        )
    )
    assert out.success
    assert out.meta["layout_id"] == "comic_strip"
    assert out.meta["recent_layout_ids"] == ["hero_cover"]
    assert "低饱和黏土摄影" in out.prompt
    final_system = b._provider.calls[-1][0]
    assert "大主题约束｜全图最高视觉约束" in final_system
    assert "整体版式只控制宏观区域" in final_system


def test_same_date_reuses_persisted_layout_without_director_call():
    persisted = {
        "layout_id": "group_court",
        "hero_topic_id": "topic-02",
        "support_topic_ids": ["topic-01"],
        "comedy_device": "一本正经地荒诞",
        "layout_reason": "同日已有选择",
    }
    b = _builder()
    out = b.build(_input(persisted_theme_meta=persisted))
    assert out.success
    assert out.meta["layout_id"] == "group_court"
    assert out.meta["layout_reused"] is True
    assert out.meta["api_call_count"] == 2  # 候选 + 最终 Prompt，无新版式调用
    assert len(b._provider.calls) == 2


def test_explicit_layout_inside_custom_style_wins_without_changing_style_text():
    custom_style = "复古报纸三栏头版"
    b = _builder()
    out = b.build(_input(image_theme="custom", image_theme_custom=custom_style))
    assert out.success
    assert out.meta["layout_id"] == "hero_cover"
    assert out.meta["style_layout_locked"] is True
    assert custom_style in out.prompt
    assert out.meta["api_call_count"] == 2

    from app.ai.image_themes import resolve_image_theme
    from app.ai.prompt_editing import replace_theme_section

    switched = replace_theme_section(out.prompt, resolve_image_theme("pink"))
    assert custom_style not in switched
    assert "粉红色" in switched
    assert len(re.findall(r"(?m)^【大主题】$", switched)) == 1
