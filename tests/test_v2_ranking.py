"""V2 P2：排行榜引擎单元测试。

验证：计数正确 / 动态 Top 上限 / 系统消息过滤 / 确定性 / 同数量稳定排序 /
中文与 Emoji 昵称 / 空消息 / ranking.json 结构。
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.base import V2Message
from app.ranking.engine import RankingEngine

engine = RankingEngine()
PERIOD_START = "2026-08-17 00:00:00"
PERIOD_END = "2026-08-17 23:59:59"


def _msg(
    sender: str,
    mtype: str = "text",
    content: str = "hi",
    i: int = 0,
    sender_id: str | None = None,
) -> V2Message:
    return V2Message(
        message_id=f"m{i}",
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id=sender_id or f"wxid_{sender}",
        sender_name=sender,
        timestamp=datetime(2026, 8, 17, 10, 0, 0),
        message_type=mtype,
        content=content,
    )


def test_count_and_top10():
    messages = [
        _msg("张三", i=1),
        _msg("张三", i=2),
        _msg("张三", i=3),
        _msg("李四", i=4),
        _msg("李四", i=5),
        _msg("王五", i=6),
    ]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert r.group_name == "测试群"
    assert r.period_start == PERIOD_START
    assert r.period_end == PERIOD_END
    assert r.message_count == 6
    assert r.speaker_count == 3
    assert [s.name for s in r.top_speakers] == ["张三", "李四", "王五"]
    assert [s.count for s in r.top_speakers] == [3, 2, 1]
    assert [s.rank for s in r.top_speakers] == [1, 2, 3]


def test_three_day_messages_are_summed_by_speaker():
    messages = [
        _msg("张三", i=1),
        _msg("张三", i=2),
        _msg("张三", i=3),
        _msg("李四", i=4),
    ]
    messages[0].timestamp = datetime(2026, 8, 14, 10, 0, 0)  # 周五
    messages[1].timestamp = datetime(2026, 8, 15, 10, 0, 0)  # 周六
    messages[2].timestamp = datetime(2026, 8, 16, 10, 0, 0)  # 周日
    messages[3].timestamp = datetime(2026, 8, 16, 11, 0, 0)

    r = engine.compute(
        messages,
        "测试群",
        "2026-08-14 00:00:00",
        "2026-08-16 23:59:59",
        top_limit=15,
    )
    assert r.message_count == 4
    assert r.speaker_count == 2
    assert (r.top_speakers[0].rank, r.top_speakers[0].name, r.top_speakers[0].count) == (
        1,
        "张三",
        3,
    )


def test_system_message_filtered():
    messages = [
        _msg("张三", i=1),
        _msg("系统", mtype="system", content="xxx 加入了群聊", i=2),
        _msg("李四", content="将群聊名称修改为", i=3),
        _msg("王五", mtype="image", i=4),  # 图片计入
    ]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert r.message_count == 2
    assert r.speaker_count == 2


def test_text_primary_counts_text_and_interactions_separately():
    messages = [
        _msg("白白", "text", i=1),
        _msg("白白", "text", i=2),
        _msg("白白", "image", i=3),
        _msg("白白", "quote", i=4),
        _msg("只发图", "image", i=5),
        _msg("系统", "system", i=6),
    ]

    result = engine.compute(
        messages,
        "测试群",
        PERIOD_START,
        PERIOD_END,
        count_policy="text_primary_with_interactions",
        name_source="wechat_data_analysis",
    )

    assert result.message_count == 5
    assert result.text_message_count == 2
    assert result.interaction_message_count == 3
    assert result.speaker_count == 2
    assert result.text_speaker_count == 1
    assert [item.name for item in result.top_speakers] == ["白白"]
    assert result.top_speakers[0].count == 2
    assert result.top_speakers[0].text_count == 2
    assert result.top_speakers[0].interaction_count == 2
    assert result.top_speakers[0].name_source == "wechat_data_analysis"


def test_text_primary_tie_does_not_use_interactions_to_change_rank():
    messages = [
        _msg("B", "text", i=1),
        _msg("B", "image", i=2),
        _msg("B", "emoji", i=3),
        _msg("A", "text", i=4),
    ]

    result = engine.compute(
        messages,
        "测试群",
        PERIOD_START,
        PERIOD_END,
        count_policy="text_primary_with_interactions",
    )

    assert [
        (item.name, item.text_count, item.interaction_count)
        for item in result.top_speakers
    ] == [
        ("A", 1, 0),
        ("B", 1, 2),
    ]


def test_identity_uses_sender_id_and_counts_supported_forward_types():
    messages = [
        _msg("同名", "red_packet", i=1, sender_id="wxid-a"),
        _msg("同名", "chat_history", i=2, sender_id="wxid-b"),
        _msg("改名前", i=3, sender_id="wxid-c"),
        _msg("改名后", i=4, sender_id="wxid-c"),
    ]
    result = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)

    assert result.message_count == 4
    assert result.speaker_count == 3
    same_name_entries = [item for item in result.top_speakers if item.name.startswith("同名（同名 ")]
    assert len(same_name_entries) == 2
    assert sum(item.count for item in same_name_entries) == 2
    renamed = next(item for item in result.top_speakers if item.name in {"改名前", "改名后"})
    assert renamed.count == 2


def test_deterministic():
    messages = [_msg(f"用户{n}", i=n) for n in range(12)]
    r1 = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    r2 = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert r1.to_dict() == r2.to_dict()


def test_tie_stable_sort():
    # 所有人消息数相同 → 按名称稳定升序
    messages = [
        _msg("赵六", i=1),
        _msg("张三", i=2),
        _msg("王五", i=3),
        _msg("李四", i=4),
    ]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert [s.name for s in r.top_speakers] == ["张三", "李四", "王五", "赵六"]


def test_tie_sort_ignores_leading_emoji_decoration():
    messages = [_msg("成员 02", i=1), _msg("成员 01", i=2)]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert [s.name for s in r.top_speakers] == ["成员 01", "成员 02"]


def test_chinese_emoji_names():
    messages = [
        _msg("示例交流群 A ✨", i=1),
        _msg("示例交流群 A ✨", i=2),
        _msg("示例UED-4群🤘", i=3),
    ]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert r.top_speakers[0].name == "示例交流群 A ✨"
    assert r.top_speakers[0].count == 2


def test_empty_messages():
    r = engine.compute([], "测试群", PERIOD_START, PERIOD_END)
    assert r.message_count == 0
    assert r.speaker_count == 0
    assert r.top_speakers == []


def test_top10_capped():
    messages = [_msg(f"成员{n}", i=n) for n in range(15)]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert len(r.top_speakers) == 10
    assert r.top_speakers[0].rank == 1
    assert r.top_speakers[-1].rank == 10


def test_top15_capped_when_requested():
    messages = [_msg(f"成员{n:02}", i=n) for n in range(20)]
    r = engine.compute(
        messages,
        "测试群",
        PERIOD_START,
        PERIOD_END,
        top_limit=15,
    )
    assert r.top_limit == 15
    assert len(r.top_speakers) == 15
    assert r.top_speakers[-1].rank == 15


def test_ranking_json_structure():
    messages = [_msg("张三", i=1), _msg("李四", i=2)]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    d = r.to_dict()
    assert set(d) == {
        "group_name",
        "period_start",
        "period_end",
        "speaker_count",
        "message_count",
        "count_policy",
        "text_message_count",
        "interaction_message_count",
        "text_speaker_count",
        "top_limit",
        "top_speakers",
    }
    assert d["top_limit"] == 10
    assert d["top_speakers"][0]["rank"] == 1
    assert d["top_speakers"][0]["name"] == "张三"
    assert d["top_speakers"][0]["count"] == 1
    assert d["top_speakers"][0]["text_count"] == 1
    assert d["top_speakers"][0]["interaction_count"] == 0
    assert d["top_speakers"][0]["name_source"] == "resolved"
    assert len(d["top_speakers"][0]["identity_key"]) == 16
