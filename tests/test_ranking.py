"""P2 测试：消息标准化 + 排行榜确定性统计。"""

from datetime import datetime

from app.providers.history.base import RawMessage
from app.services.message_normalizer import MessageNormalizer, normalize_messages
from app.services.ranking_service import RankingEngine


def _msg(sender: str, mtype: str = "text", content: str = "hi", ts: str = "2026-08-10T10:00:00") -> RawMessage:
    return RawMessage(
        group_id="group-a",
        group_name="测试群",
        sender_id=f"id-{sender}",
        sender_name=sender,
        timestamp=datetime.fromisoformat(ts),
        message_type=mtype,
        content=content,
        source="mock_fixture",
    )


def test_system_messages_filtered():
    msgs = [
        _msg("张三", content="\"李四\"邀请\"王五\"加入了群聊"),
        _msg("系统", mtype="system", content="群名称修改"),
        _msg("张三", content="正常消息"),
    ]
    normalized = normalize_messages(msgs)
    countable = [m for m in normalized if m.countable]
    assert len(countable) == 1
    assert countable[0].content == "正常消息"


def test_all_user_types_countable():
    msgs = [
        _msg("A", "text"),
        _msg("A", "image", "[图片]"),
        _msg("A", "emoji", "[表情]"),
        _msg("A", "voice", "[语音]"),
        _msg("A", "video", "[视频]"),
        _msg("A", "file", "[文件]"),
        _msg("A", "link", "https://x.com"),
        _msg("A", "quote", "引用内容"),
        _msg("A", "red_packet", "[红包]"),
        _msg("A", "transfer", "[转账]"),
        _msg("A", "system", "系统消息"),
    ]
    normalized = normalize_messages(msgs)
    countable = sum(1 for m in normalized if m.countable)
    assert countable == 10


def test_consecutive_messages_not_merged():
    msgs = [_msg("A", "text", "1"), _msg("A", "text", "2"), _msg("A", "text", "3")]
    normalized = normalize_messages(msgs)
    engine = RankingEngine()
    result = engine.compute(normalized, "测试群", "s", "e")
    assert result.total_messages == 3


def test_ranking_numbers():
    msgs = [
        _msg("张三", "text", "a", "2026-08-10T09:00:00"),
        _msg("李四", "text", "b", "2026-08-10T09:00:01"),
        _msg("张三", "image", "[图片]", "2026-08-10T09:00:02"),
        _msg("系统", "system", "入群提醒", "2026-08-10T09:00:03"),
        _msg("王五", "text", "c", "2026-08-10T09:00:04"),
        _msg("张三", "text", "d", "2026-08-10T09:00:05"),
    ]
    normalized = normalize_messages(msgs)
    engine = RankingEngine()
    result = engine.compute(normalized, "测试群", "start", "end")
    assert result.total_messages == 5
    assert result.speaker_count == 3
    assert result.top10[0] == ("张三", 3)
    assert result.top10[1][0] == "李四"
    assert result.top10[2][0] == "王五"


def test_ranking_render_format():
    engine = RankingEngine()
    normalized = normalize_messages([_msg("张三", "text", "a")])
    result = engine.compute(
        normalized,
        "Eason张UED-4群🤘",
        "2026-08-16 00:00:00",
        "2026-08-16 23:59:59",
    )
    text = result.render()
    assert "消息统计" in text
    assert "时间起：2026-08-16 00:00:00" in text
    assert "发言人数：1" in text
    assert "总消息：1" in text
    assert "发言 Top10" in text
    assert "1.张三【1】" in text


def test_deterministic_tie_break():
    msgs = [_msg("乙", "text", "a"), _msg("甲", "text", "b"), _msg("丙", "text", "c")]
    normalized = normalize_messages(msgs)
    engine = RankingEngine()
    r1 = engine.compute(normalized, "g", "s", "e")
    r2 = engine.compute(normalized, "g", "s", "e")
    assert r1.render() == r2.render()
