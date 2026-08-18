"""V2 ImagePromptBuilder（DeepSeek V4 Flash）实现。

输入：标准化聊天内容 + 群名 + 统计周期 + 消息数 + 发言人数 + 生图 Prompt 模板
输出：image_prompt.txt（可直接交给 Codex `$imagegen` / GPT Image 2）

策略：
- 复用 V1 DeepSeekV4FlashProvider 的底层 HTTP 调用（重试/超时）；
- 固定使用 V4 Flash（settings.ai_model，默认 deepseek-chat）；
- 模板（templates/image_prompt/）控制最终 Prompt 的输出结构，可编辑；
- 超长聊天采用「分块 → 逐块提取事件(JSON) → 合并去重 → 按模板生成」，
  避免简单暴力截断丢失重要内容；
- 模型调用结构化元数据写入 meta（不含 API Key）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from app.ai.prompt_templates import (
    ImagePromptTemplateError,
    ImagePromptTemplateService,
    render_image_prompt_template,
    validate_image_prompt_template,
)
from app.ai.prompt_builder_types import PromptInput, PromptOutput
from app.config.settings import Settings, get_settings
from app.providers.ai.deepseek import DeepSeekV4FlashProvider

logger = logging.getLogger("groupbrief.ai")

SYSTEM_BASE = """你是「群报 GroupBrief」的漫画日报海报 Prompt 设计师。
你的唯一任务：根据给定的微信群聊内容，生成一份可以直接复制给 GPT 图片生成能力的完整中文 Prompt，
用于绘制「竖版微信群日报漫画信息图」。

硬性要求（必须严格遵守）：
1. 只能使用聊天内容中真实存在的事件、人物、对话，禁止编造任何聊天中不存在的事件。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 原话引用必须来自真实聊天，可适当缩写，但不能改写事实。
4. 可以幽默化标题，但不能改变事实。
5. 海报人物依据「聊天事件中提到的人物」，而不是发言排行榜 Top10。
6. 数据（消息数、发言人数）必须使用给定数字，禁止自行计算。
7. 必须严格按给定的【输出结构】组织最终 Prompt，不得改变结构。"""

CHUNK_ANALYZE_SYSTEM = """你是群聊事件分析助手。只提取聊天中真实存在的事件/人物/原话，
输出严格 JSON（不输出其他内容），没有事件就返回空数组。"""

CHUNK_ANALYZE_PROMPT = """以下是微信群聊记录片段（{label}）。

请分析并输出 JSON：
{{
  "events": [
    {{"title": "事件短标题", "people": ["提到的人名"], "content": "事件描述（真实基于聊天）", "quotes": ["1-3条真实原话或改写原话"]}}
  ]
}}

要求：只提取真实存在的内容；没有事件就返回空数组；不超过 6 个事件。"""

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """剥离模板中的 HTML 注释（供作者写说明，不进入最终 Prompt）。"""
    return _HTML_COMMENT_RE.sub("", text).strip()


_MEDIA_PREFIX = {
    "image": "[图片]",
    "emoji": "[表情]",
    "voice": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "red_packet": "[红包]",
    "transfer": "[转账]",
}


def _to_ai_text(m) -> str:
    content = m.content or ""
    prefix = _MEDIA_PREFIX.get(m.message_type, "")
    if not content and prefix:
        return prefix
    if prefix and not content.startswith("["):
        return f"{prefix} {content}"
    return content


class DeepSeekImagePromptBuilder:
    name = "deepseek-image-prompt"

    def __init__(
        self,
        settings: Settings | None = None,
        templates: ImagePromptTemplateService | None = None,
        provider: DeepSeekV4FlashProvider | None = None,
    ):
        self.settings = settings or get_settings()
        self.templates = templates or ImagePromptTemplateService()
        # 复用 V1 底层调用（重试/超时/模型），不重复实现
        self._provider = provider or DeepSeekV4FlashProvider(self.settings)

    # ---------- 对外 ----------

    def build(self, data: PromptInput) -> PromptOutput:
        api_model = self._provider.model
        try:
            template_text = _strip_html_comments(self.templates.read(data.template))
            validate_image_prompt_template(template_text)
            structure = render_image_prompt_template(
                template_text,
                {
                    "group_name": data.group_name,
                    "period_start": data.period_start,
                    "period_end": data.period_end,
                    "message_count": str(data.message_count),
                    "speaker_count": str(data.speaker_count),
                },
            )

            lines = [self._to_line(m) for m in data.messages]
            chunks = self._chunk(lines)
            meta: dict = {
                "template": data.template,
                "api_model": api_model,
                "message_lines": len(lines),
                "chunk_count": len(chunks),
                "generated_at": datetime.now().isoformat(),
            }

            if len(chunks) <= 1:
                meta["mode"] = "single"
                user = "以下是群聊记录：\n\n" + "\n".join(lines)
                text = self._chat(structure, user)
            else:
                meta["mode"] = "chunked"
                analyses: list[str] = []
                for idx, chunk in enumerate(chunks, start=1):
                    label = f"第 {idx}/{len(chunks)} 块"
                    analyses.append(
                        self._chat(
                            CHUNK_ANALYZE_SYSTEM,
                            CHUNK_ANALYZE_PROMPT.format(label=label) + "\n\n聊天记录：\n" + chunk,
                        )
                    )
                text = self._chat(
                    structure,
                    "以下是多个聊天片段的 JSON 事件分析，请去重合并（相似事件合并，保留真实细节），"
                    "并按给定的【输出结构】生成完整生图 Prompt：\n\n"
                    + "\n\n".join(analyses),
                )

            return PromptOutput(success=True, prompt=text.strip(), model="deepseek-v4-flash", meta=meta)
        except (ImagePromptTemplateError, ValueError) as e:
            logger.warning("Prompt 模板错误：%s", e)
            return PromptOutput(success=False, error=str(e)[:300], model="deepseek-v4-flash")
        except Exception as e:  # DeepSeek 调用失败等
            logger.exception("ImagePromptBuilder 生成失败")
            return PromptOutput(success=False, error=str(e)[:300], model="deepseek-v4-flash")

    # ---------- 内部 ----------

    def _to_line(self, m) -> str:
        ts = m.timestamp.strftime("%H:%M") if hasattr(m.timestamp, "strftime") else ""
        return f"[{ts}] {m.sender_name or '(未知)'}: {_to_ai_text(m)}"

    def _chunk(self, lines: list[str]) -> list[str]:
        if not lines:
            return []
        size = max(1, self.settings.chunk_message_count)
        return ["\n".join(lines[i : i + size]) for i in range(0, len(lines), size)]

    def _chat(self, structure: str, user_prompt: str) -> str:
        """调用 DeepSeek V4 Flash。system 含固定约束 + 模板输出结构。"""
        system = SYSTEM_BASE + "\n\n【输出结构】\n" + structure
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        return self._provider._chat(messages)
