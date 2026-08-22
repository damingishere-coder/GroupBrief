"""生成 fixtures 模拟聊天数据。

与正式数据模型（RawMessage 结构）完全一致，用于在无法读取真实微信时
继续开发排行榜 / DeepSeek / 邮件 / UI 全链路。

用法：
    python scripts/generate_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES = PROJECT_ROOT / "fixtures"

GROUPS = [
    {
        "group_id": "group-a",
        "group_name": "示例交流群 A",
        "member_count": 48,
    },
    {
        "group_id": "group-b",
        "group_name": "示例交流群 B",
        "member_count": 112,
    },
]

MEMBERS_A = [(f"wx_a_{index:03d}", f"成员 A{index:02d}") for index in range(1, 16)]

MEMBERS_B = [(f"wx_b_{index:03d}", f"成员 B{index:02d}") for index in range(1, 9)]

TEXTS_A = [
    "今天这个需求排期可以过吗？",
    "可以，下午评审",
    "@成员 A13 这个交互要改一下",
    "截图我发群里了，大家看看",
    "哈哈哈哈笑死",
    "这个方案我反对，成本太高",
    "B站那个视频你们看了吗，太好笑了",
    "谁有网盘的会员借一下",
    "周末要不要组个局打桌游",
    "我这周加班到爆炸",
    "新来的设计好厉害",
    "老板今天又画饼了",
    "【重要】明天下午全员大会",
    "这个链接里的小作文绝了",
    "不行不行，这个配色太丑了",
    "下周出差北京，有一起的吗",
    "刚看到个段子：",
    "咱们组这个月绩效稳了",
    "@成员 A14 你那边的接口好了没",
    "好了好了，刚发布",
    "太卷了太卷了",
    "下班！冲！",
    "群里谁认识做插画的朋友",
    "成员 A13 发红包了！！",
    "抢到了！谢谢成员 A13",
]

TEXTS_B = [
    "周会纪要我发群里了",
    "这个月 KPI 大家怎么看",
    "成员 B03 那个原型图很赞",
    "@成员 B05 数据看板什么时候能上",
    "用户反馈说加载太慢了",
    "竞品昨天发版了，功能比我们多",
    "灰度方案要再评审一下",
    "大家把简历发我，内推几个岗位",
    "成员 B06 今天过生日，晚上一起吃饭",
    "这个 Bug 我先复现一下",
    "老板批了预算，可以招人了",
    "周末谁去爬山",
    "需求文档我更新到最新版本了",
    "这个交互总感觉差点意思",
    "上线时间定了，下周三",
    "大家注意，测试环境要重启",
    "收到收到",
    "这个页面加载 3 秒，太离谱",
    "成员 B04 新做的原型大家看看",
    "今晚团建，老地方火锅",
    "新人介绍下自己吧",
    "大家好，我是新来的运营小林",
    "欢迎欢迎",
    "花名想好了吗",
    "就叫豆包吧，好记",
]

LINK_A = [
    "https://www.bilibili.com/video/BV1xx411c7mD 这个视频笑死我了",
    "https://www.zhihu.com/question/123456789 知乎热榜这个回答绝了",
    "https://mp.weixin.qq.com/s/xxxx 公众号这篇文章值得一看",
]

SYSTEM_TEXTS = [
    "\"张三\"邀请\"李四\"加入了群聊",
    "\"王五\"退出群聊",
    "\"赵六\"撤回了一条消息",
    "群名称由\"测试群\"变更为\"示例交流群 A\"",
]


def gen_hash(source: str, idx: int) -> str:
    return hashlib.md5(f"{source}:{idx}".encode()).hexdigest()


def gen_day(group, members, day: date) -> list[dict]:
    rnd = random.Random(f"{group['group_id']}-{day.isoformat()}")
    messages: list[dict] = []
    total = rnd.randint(280, 900)
    idx = 0

    for _ in range(total):
        sender_id, sender_name = rnd.choice(members)
        hour = rnd.randint(7, 23)
        minute = rnd.randint(0, 59)
        second = rnd.randint(0, 59)
        ts = datetime.combine(day, time(hour, minute, second))

        roll = rnd.random()
        if roll < 0.62:
            mtype, content = "text", rnd.choice(TEXTS_A if group["group_id"] == "group-a" else TEXTS_B)
        elif roll < 0.70:
            mtype, content = "image", "[图片]"
        elif roll < 0.75:
            mtype, content = "emoji", "[表情]"
        elif roll < 0.80:
            mtype, content = "link", rnd.choice(LINK_A)
        elif roll < 0.85:
            mtype, content = "quote", f"引用：{rnd.choice(TEXTS_A if group['group_id'] == 'group-a' else TEXTS_B)[:20]}"
        elif roll < 0.90:
            mtype, content = "red_packet", "[红包]"
        elif roll < 0.93:
            mtype, content = "voice", "[语音]"
        elif roll < 0.96:
            mtype, content = "system", rnd.choice(SYSTEM_TEXTS)
        elif roll < 0.98:
            mtype, content = "file", "[文件] 需求文档v3.docx"
        else:
            mtype, content = "video", "[视频]"

        idx += 1
        messages.append(
            {
                "group_id": group["group_id"],
                "group_name": group["group_name"],
                "sender_id": sender_id,
                "sender_name": sender_name,
                "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "message_type": mtype,
                "content": content,
                "source": "mock_fixture",
                "source_message_id": f"{group['group_id']}-{day.isoformat()}-{idx}",
                "content_hash": gen_hash(content, idx),
            }
        )

    messages.sort(key=lambda m: m["timestamp"])
    return messages


def main() -> None:
    groups_file = FIXTURES / "groups.json"
    groups_file.parent.mkdir(parents=True, exist_ok=True)
    groups_file.write_text(json.dumps(GROUPS, ensure_ascii=False, indent=2), encoding="utf-8")

    day = date.today()
    for group in GROUPS:
        safe = "".join(c for c in group["group_id"] if c.isalnum() or c in "-_")
        for offset in range(0, 8):
            d = day - timedelta(days=offset)
            data = gen_day(group, MEMBERS_A if group["group_id"] == "group-a" else MEMBERS_B, d)
            out = FIXTURES / "messages" / safe / f"{d.isoformat()}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  {safe}/{d.isoformat()}.json  {len(data)} 条")

    print("fixtures 生成完成")


if __name__ == "__main__":
    main()
