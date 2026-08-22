"""Template Prompt Provider（本地模板生成，不调用任何 LLM）。

在群聊总结主备模型都不可用时使用：
- 完全基于真实统计与消息内容
- 不编造事件（只引用真实出现的消息片段与人名）
- 输出结构与模型版本一致，保证邮件/UI/文件全链路可交付
"""

from __future__ import annotations

from collections import Counter

from app.providers.ai.base import (
    ImagePromptResult,
    PromptContext,
    PromptGeneratorProvider,
)

BOARD_TOPIC_POOL = [
    "职场日常",
    "群内互动",
    "工作推进",
    "生活分享",
    "项目进展",
    "团队动态",
    "欢乐瞬间",
    "干货讨论",
]


class TemplatePromptProvider(PromptGeneratorProvider):
    name = "template"
    model = "template-v1"

    def health_check(self) -> tuple[bool, str]:
        return True, "模板生成器（无需 API Key）"

    def generate_image_prompt(self, context: PromptContext) -> ImagePromptResult:
        lines = context.messages_text.split("\n")
        texts: list[str] = [l for l in lines if l.strip()]
        speakers = self._top_speakers(lines)

        prompt = f"""【任务】
生成一张竖版微信群日报漫画信息图。

【群名称】
{context.group_name}

【统计时间】
{context.range_start} ~ {context.range_end}

【数据】
{context.total_messages} 条消息
{context.speaker_count} 人发言

【主标题】
{self._title(context)}

【副标题】
{self._subtitle(context)}

【整体视觉】
竖版海报，蓝白主色调，漫画信息图风格，顶部大标题，中部按事件分区，
底部数据条。画面明快、留白充足、中文大字排版。

【版面1】
标题：今日群内气氛
事件：群成员今日共发言 {context.total_messages} 条，{context.speaker_count} 人参与互动。
代表人物：{speakers or "群成员"}
建议画面：聊天气泡 + 数字元素构成的漫画场景
可用文字：{context.total_messages} 条消息 · {context.speaker_count} 人发言

【版面2】
标题：今日活跃群友
事件：以下成员今日发言最多（来自真实统计）。
代表人物：{", ".join(speakers)}
建议画面：卡通头像排名的领奖台构图
可用文字：{self._speaker_line(speakers)}

【版面3】
标题：今日话题摘录
事件：以下内容为群内今日真实消息片段摘录。
代表人物：无特定人物（内容摘录）
建议画面：便签/对话框拼贴构图
可用文字：{self._quote(texts)}

【底部总结】
{context.weekdays_text or "群报 GroupBrief｜数据全部来自真实聊天统计"}

【硬性要求】
1. 只使用上述真实数据与摘录，禁止编造聊天中不存在的事件、人物、金额、时间、地点。
2. 原话摘录必须来自真实聊天记录，不得杜撰。
3. 幽默化仅限标题文案，不得改变事实。
"""

        return ImagePromptResult(True, prompt.strip(), provider=self.name, model=self.model)

    @staticmethod
    def _top_speakers(lines: list[str], n: int = 5) -> list[str]:
        counter: Counter[str] = Counter()
        for line in lines:
            if "] " in line and ":" in line:
                rest = line.split("] ", 1)[1]
                if ": " in rest:
                    name = rest.split(": ", 1)[0]
                    counter[name] += 1
        return [name for name, _ in counter.most_common(n)]

    @staticmethod
    def _speaker_line(speakers: list[str]) -> str:
        if not speakers:
            return "群内活跃成员"
        return "、".join(speakers[:5])

    @staticmethod
    def _quote(lines: list[str]) -> str:
        quotes = [l.split(": ", 1)[1][:40] if ": " in l else l[:40] for l in lines[:5]]
        return "；".join(quotes)[:150]

    @staticmethod
    def _title(context: PromptContext) -> str:
        if context.weekdays_text:
            return "群里热闹这两天！"
        return "群聊日报｜今日热聊回顾"

    @staticmethod
    def _subtitle(context: PromptContext) -> str:
        return f"{context.group_name} · {context.range_start[:10]} 群报"
