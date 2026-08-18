"""V2 P2：排行榜引擎单元测试。

验证：计数正确 / Top10 / 系统消息过滤 / 确定性 / 同数量稳定排序 /
中文与 Emoji 昵称 / 空消息 / ranking.json 结构。
"""

from __future__ import annotations

from datetime import datetime

from app.data_sources.base import V2Message
from app.ranking.engine import RankingEngine

engine = RankingEngine()
PERIOD_START = "2026-08-17 00:00:00"
PERIOD_END = "2026-08-17 23:59:59"


def _msg(sender: str, mtype: str = "text", content: str = "hi", i: int = 0) -> V2Message:
    return V2Message(
        message_id=f"m{i}",
        group_id="g1@chatroom",
        group_name="测试群",
        sender_id=f"wxid_{sender}",
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


def test_chinese_emoji_names():
    messages = [
        _msg("茶馆V3.0（三周年纪念）🐮🐴", i=1),
        _msg("茶馆V3.0（三周年纪念）🐮🐴", i=2),
        _msg("Eason张UED-4群🤘", i=3),
    ]
    r = engine.compute(messages, "测试群", PERIOD_START, PERIOD_END)
    assert r.top_speakers[0].name == "茶馆V3.0（三周年纪念）🐮🐴"
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
        "top_speakers",
    }
    assert d["top_speakers"][0] == {"rank": 1, "name": "张三", "count": 1}
