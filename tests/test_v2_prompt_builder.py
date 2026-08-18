"""V2 P4：ImagePromptBuilder 单元测试。

通过注入 FakeDeepSeek（实现 _chat 契约）隔离真实 API，
验证：模板渲染 / 输入组装 / 分块策略 / 元数据 / 失败路径。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.ai.prompt_builder import DeepSeekImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.ai.prompt_templates import (
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


def _input(messages=None, template="default") -> PromptInput:
    return PromptInput(
        group_name="茶馆V3.0（三周年纪念）",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        message_count=3,
        speaker_count=2,
        messages=messages or [_msg("张三", "今天群里聊了票房", i=1), _msg("李四", "《牛来》破500万了", i=2)],
        template=template,
    )


class FakeDeepSeek:
    """模拟 DeepSeek 底层 _chat。"""

    def __init__(self, model="deepseek-chat", raise_on: Exception | None = None):
        self.model = model
        self.calls: list[tuple[str, str]] = []
        self.raise_on = raise_on

    def _chat(self, messages: list[dict]) -> str:
        if self.raise_on:
            raise self.raise_on
        system = messages[0]["content"]
        user = messages[1]["content"]
        self.calls.append((system, user))
        # 模拟：按输出结构返回
        structure_block = system.split("【输出结构】", 1)[-1]
        return f"生成成功\n{structure_block[:60]}"


def _builder(fake: FakeDeepSeek | None = None, tmp_path=None):
    return DeepSeekImagePromptBuilder(
        provider=fake or FakeDeepSeek(),
        templates=ImagePromptTemplateService(templates_dir=(tmp_path / "image_prompt") if tmp_path else None),
    )


def test_build_success_returns_prompt():
    b = _builder()
    out = b.build(_input())
    assert out.success is True
    assert out.prompt.startswith("生成成功")
    assert out.model == "deepseek-v4-flash"
    assert out.meta is not None
    assert out.meta["mode"] == "single"
    assert out.meta["api_model"] == "deepseek-chat"
    assert "template" in out.meta


def test_build_input_contains_group_and_data():
    b = _builder()
    out = b.build(_input())
    assert out.success
    system, user = b._provider.calls[0]
    assert "茶馆V3.0（三周年纪念）" in system  # 模板变量已渲染进输出结构
    assert "3 条消息" in system  # 数据渲染进输出结构
    assert "2 人发言" in system
    assert "以下是群聊记录" in user  # 用户输入含聊天内容
    assert "张三" in user


def test_build_media_type_prefix():
    messages = [_msg("张三", "", mtype="image", i=1), _msg("李四", "", mtype="voice", i=2)]
    b = _builder()
    out = b.build(_input(messages=messages))
    assert out.success
    _, user = b._provider.calls[0]
    assert "[图片]" in user
    assert "[语音]" in user


def test_chunk_strategy_for_long_chat():
    # 构造超过 chunk_size 的消息，验证进入 chunked 模式并调用多次
    messages = [_msg(f"用户{n}", f"消息{n}", i=n) for n in range(150)]
    b = _builder()
    b.settings.chunk_message_count = 60
    out = b.build(_input(messages=messages))
    assert out.success
    assert out.meta["mode"] == "chunked"
    assert out.meta["chunk_count"] == 3
    # 3 次块分析 + 1 次合并 = 4 次调用
    assert len(b._provider.calls) == 4
    # 首次调用是块分析（含 JSON 事件提取提示）
    assert "请分析并输出 JSON" in b._provider.calls[0][1]


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
