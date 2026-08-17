"""P4 测试：Prompt 生成（模板 + DeepSeek chunk 逻辑）。"""

from datetime import datetime

from app.providers.ai.base import PromptContext
from app.providers.ai.template import TemplatePromptProvider
from app.providers.history.mock import MockProvider
from app.scheduler.calendar_rules import get_report_window
from app.services.message_normalizer import normalize_messages
from app.services.prompt_service import PromptService
from app.services.ranking_service import RankingEngine


def _fetch_norm(group_id: str, day_str: str = "2026-08-13"):
    start = datetime.fromisoformat(f"{day_str}T00:00:00")
    end = datetime.fromisoformat(f"{day_str}T23:59:59")
    result = MockProvider().fetch_messages(group_id, start, end)
    return normalize_messages(result.messages)


def _sample_context() -> PromptContext:
    normalized = _fetch_norm("group-a")
    rank = RankingEngine().compute(normalized, "Eason张UED-4群", "2026-08-13 00:00:00", "2026-08-13 23:59:59")
    lines = [
        f"[{m.timestamp.strftime('%H:%M')}] {m.sender_name}: {m.ai_text}"
        for m in normalized[:40]
        if m.countable
    ]
    return PromptContext(
        group_id="group-a",
        group_name="Eason张UED-4群",
        report_date="2026-08-14",
        range_start="2026-08-13 00:00:00",
        range_end="2026-08-13 23:59:59",
        total_messages=rank.total_messages,
        speaker_count=rank.speaker_count,
        messages_text="\n".join(lines),
    )


def test_template_prompt_structure():
    provider = TemplatePromptProvider()
    ok, _ = provider.health_check()
    assert ok
    result = provider.generate_image_prompt(_sample_context())
    assert result.success
    text = result.prompt
    for section in [
        "【任务】", "【群名称】", "【统计时间】", "【数据】", "【主标题】",
        "【整体视觉】", "【版面1】", "【版面2】", "【版面3】", "【底部总结】", "【硬性要求】",
    ]:
        assert section in text, f"缺少 {section}"
    # 数据必须来自真实统计
    assert "条消息" in text


def test_template_uses_real_speakers():
    provider = TemplatePromptProvider()
    context = _sample_context()
    result = provider.generate_image_prompt(context)
    # Top 发言者（来自真实统计）应出现在版面2
    normalized = _fetch_norm("group-a")
    top = [name for name, _ in RankingEngine().compute(normalized, "g", "s", "e").top10[:3]]
    assert top, "fixture 应有发言者"
    assert top[0] in result.prompt


def test_deepseek_chunking():
    """验证 chunk 逻辑：消息数 > chunk_size 时分成多块。"""
    from app.providers.ai.deepseek import DeepSeekV4FlashProvider
    from app.config.settings import Settings

    settings = Settings(ai_api_key="test-key", chunk_message_count=20)
    provider = DeepSeekV4FlashProvider(settings)
    lines = [f"[10:0{i}] 广州: 消息{i}" for i in range(45)]
    chunks = provider._chunk_messages(lines)
    assert len(chunks) == 3
    assert len(chunks[0].text.split("\n")) == 20
    assert len(chunks[-1].text.split("\n")) == 5


def test_deepseek_no_key_skips_to_template():
    """未配置 API Key 时 PromptService 使用模板 Provider。"""
    from app.config.settings import Settings

    settings = Settings(ai_api_key="")
    service = PromptService(settings)
    provider = service._get_provider()
    assert provider.name == "template"


def test_prompt_service_generates_via_template():
    from app.db.models import Group

    service = PromptService()
    window = get_report_window(datetime.fromisoformat("2026-08-14").date())
    normalized = _fetch_norm("group-b")
    rank = RankingEngine().compute(normalized, "产品经理交流群", "s", "e")
    outcome = service.generate(
        Group(display_name="产品经理交流群", wechat_group_id="group-b"),
        window, rank, normalized,
    )
    assert outcome.success
    assert "【任务】" in outcome.prompt
