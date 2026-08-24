"""V2 ImagePromptBuilder（Codex GPT 主用、DeepSeek 备用）实现。

输入：标准化聊天内容 + 群名 + 统计周期 + 消息数 + 发言人数 + 生图 Prompt 模板
输出：image_prompt.txt（可直接交给 Codex `$imagegen` / GPT Image 2）

策略：
- 复用 V1/V2 共用的 Codex GPT / DeepSeek 主备调用（重试/超时）；
- 主模型固定使用 settings.codex_summary_model（默认 gpt-5.6-sol）；
- 模板（templates/image_prompt/）控制最终 Prompt 的输出结构，可编辑；
- 超长聊天采用「分块 → 逐块提取事件(JSON) → 合并去重 → 按模板生成」，
  避免简单暴力截断丢失重要内容；
- 模型调用结构化元数据写入 meta（不含 API Key）。
"""

from __future__ import annotations

import logging
import re
from time import perf_counter
from datetime import datetime

from app.ai.prompt_templates import (
    ImagePromptTemplateError,
    ImagePromptTemplateService,
    render_image_prompt_template,
    validate_image_prompt_template,
)
from app.ai.prompt_builder_types import PromptInput, PromptOutput
from app.ai.image_themes import ImageThemeError, resolve_image_theme
from app.ai.layouts import (
    IMAGE_LAYOUT_DEFINITIONS,
    LAYOUT_DIRECTOR_SYSTEM,
    LayoutPlan,
    LayoutPlanError,
    build_layout_director_prompt,
    detect_explicit_style_layout,
    fallback_layout_plan,
    fixed_layout_plan,
    layout_plan_json,
    parse_layout_plan,
    preferred_layout_from_style,
    resolved_layout_instruction,
    restored_layout_plan,
    selected_topic_ids,
)
from app.ai.concurrency import normalized_limit, run_ai_tasks_ordered
from app.ai.conversation_segments import (
    HARD_CHUNK_CHARS,
    OVERLAP_MESSAGES,
    SESSION_GAP_MINUTES,
    TARGET_CHUNK_CHARS,
    ConversationChunk,
    PromptMessage,
    segment_messages,
)
from app.ai.deepseek_events import (
    EVENT_ANALYZE_SYSTEM,
    build_event_prompt,
    deduplicate_event_cards,
    parse_event_cards,
)
from app.ai.topic_selection import (
    TOPIC_CANDIDATE_SYSTEM,
    TopicSelectionError,
    build_direct_candidate_prompt,
    build_merged_candidate_prompt,
    parse_topic_candidates,
    score_and_select_topics,
    selected_topics_json,
)
from app.config.settings import Settings, get_settings
from app.providers.ai.codex import build_summary_provider

logger = logging.getLogger("groupbrief.ai")

STRUCTURED_ANALYSIS_MAX_ATTEMPTS = 2
TOPIC_CANDIDATE_MAX_TOKENS = 8_000
EVENT_CARD_MAX_TOKENS = 6_000
LAYOUT_DIRECTOR_MAX_TOKENS = 1_800
FINAL_PROMPT_MAX_ATTEMPTS = 2
_STRUCTURED_RETRY_INSTRUCTION = """\

上一次响应不完整、被截断或不符合约定 JSON。请重新阅读原材料，并重新输出一个完整、紧凑、可解析的 JSON 对象。
不要复述要求，不要输出 Markdown；缩短标题、摘要和理由，但保留真实 message_ids 与所有必需字段。"""
_LAYOUT_RETRY_INSTRUCTION = """\

上一次分镜响应不完整或不符合 JSON 约定。请重新输出完整紧凑的 JSON 对象；
必须使用合法 layout_id 和 structure_mode；featured_topic_ids 数量必须匹配结构模式，
topic_order 与 panel_beats 必须恰好覆盖全部入选主题；至少一个话题使用两个镜头，镜头总数必须合法。"""

_FINAL_PROMPT_RETRY_INSTRUCTION = """\

上一次最终 Prompt 暴露了内部字段名、主题 ID，或退化成等大模块列表。请完整重写：
按“景别 + 人物动作 + 群友反应或道具特写 + 逐字气泡”写每个话题；保留全部已选话题、指定画风、统计日期与漫画分镜；
每段指定文字只出现一次，不要输出任何数据字段式栏目名、英文装饰词、Logo、网址或 topic ID，也不要把一个话题机械装进一个等大的矩形区域。"""

SYSTEM_BASE = """你是「群报 GroupBrief」的漫画日报海报 Prompt 设计师。
你的唯一任务：根据给定的微信群聊内容，生成一份可以直接复制给 GPT 图片生成能力的完整中文 Prompt，
用于绘制「竖版微信群日报漫画信息图」。

硬性要求（必须严格遵守）：
1. 只能使用聊天内容中真实存在的事件、人物、对话，禁止编造任何聊天中不存在的事件。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 原话引用必须来自真实聊天，可适当缩写，但不能改写事实。
4. 事实真实性是准入门槛；通过真实性校验后，好玩程度、群内识别度和视觉笑点是第一优化目标。
   可以使用字面化、反差、回环、误会与反转、一本正经地荒诞，但不能改变事实。
5. 海报人物只能采用程序从对应消息回查得到的真实姓名，不得只画匿名人物或自由生成人名。
6. 数据（消息数、发言人数）必须使用给定数字，禁止自行计算。
7. 必须严格按给定的【输出结构】组织最终 Prompt；给定的漫画分镜骨架控制整张图的大小格与阅读节奏。
8. 候选主题已经过证据校验和程序评分；最终只能使用给定的 2～7 个入选主题，并且每个恰好使用一次。
9. 【大主题】是全图最高视觉约束，控制配色、画材、服装、造型、装饰、纹理、光影和画风；
   漫画分镜只控制格子几何、阅读路径和镜头节拍，不得替换或削弱【大主题】。
10. 一个话题不等于一个矩形模块；5～7 个话题可以展开为 7～12 个镜头，至少一个话题使用连续镜头。
11. 每段内容必须写成“景别 + 人物动作 + 群友反应或道具特写 + 逐字气泡”，不得只给抽象总结。
12. 每个话题只显示一个不超过 12 个汉字的自然短标题、一个完整真实姓名、一句不超过 24 个汉字的事实短句，
    以及默认一条不超过 22 个汉字的真实主气泡；只有连续镜头确有需要时才允许第二条短气泡。
13. 所有指定文字必须逐字且恰好出现一次；禁止输出内部字段名、topic ID、表格栏目、说明性标签、
    自动创造的栏目名、英文装饰词、Logo 或网址。
14. 海报用于微信手机端，画布固定为 1024×1536 竖版；关键文字避开四周安全边距，缩略图优先看清主标题、日期和两项统计。
15. 空间不足时严格依次减少装饰、底部总结、副标题、次要气泡；日期、两项统计、全部话题、完整姓名、事实短句和主气泡不可删除。
16. 重新生图只允许改变所选美术家族及当天解析出的视觉细节；聊天事实、日期、数字、人物、气泡、话题覆盖和既定分镜不得改变。
17. 格子必须有明显的大、中、小三级尺寸差，并按计划使用嵌套特写、连续动作或跨格主体；
    禁止整齐两列等高矩形和“每个话题一块”的列表式构图。
18. 必须把给定的“统计日期：YYYY-MM-DD”作为清晰可见的画面文字，放在海报顶部或底部，不得省略或改写。"""

CHUNK_ANALYZE_SYSTEM = """你是群聊事件分析助手。只提取聊天中真实存在的事件/人物/原话，
输出严格 JSON（不输出其他内容），没有事件就返回空数组。"""

CHUNK_ANALYZE_PROMPT = """以下是微信群聊记录片段（{label}）。

请分析并输出 JSON：
{{
  "events": [
    {{"title": "事件短标题", "people": ["提到的人名"], "content": "事件描述（真实基于聊天）", "quotes": ["1-3条逐字真实原话"]}}
  ]
}}

要求：只提取真实存在的内容；没有事件就返回空数组；每个片段最多提取 10 个事件，最终候选最多 10 个。"""

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FORBIDDEN_FINAL_PROMPT_TERMS = ("参与群友", "事实信息", "真实原话", "信息卡", "topic-")


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
    "chat_history": "[聊天记录]",
    "transfer": "[转账]",
}


def _to_ai_text(m) -> str:
    content = m.content or ""
    prefix = _MEDIA_PREFIX.get(m.message_type, "")
    if m.message_type in {"image", "file"}:
        return prefix
    if not content and prefix:
        return prefix
    if prefix and not content.startswith("["):
        return f"{prefix} {content}"
    return content


def _compact_visible_text(value: object, maximum: int) -> str:
    """把事实或原话压成单行；只截显示文本，不改变证据与完整姓名。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= maximum:
        return text
    for marker in ("。", "！", "？", "；"):
        position = text.rfind(marker, 0, maximum + 1)
        if position >= max(12, maximum // 2):
            return text[: position + 1]
    return text[: maximum - 1].rstrip() + "…"


def build_grounded_story_material(selection: dict, topic_order: tuple[str, ...]) -> str:
    """把证据整理成自然剧情句；不把内部数据字段交给图片模型绘制。"""
    selected = [
        item
        for item in selection.get("candidates", [])
        if isinstance(item, dict) and item.get("selected")
    ]
    if not selected:
        raise ValueError("没有可生成漫画剧情的入选主题")
    by_id = {str(item.get("topic_id") or ""): item for item in selected}
    ordered = [by_id[topic_id] for topic_id in topic_order if topic_id in by_id]
    if len(ordered) != len(selected):
        raise ValueError("漫画阅读顺序没有覆盖全部入选主题")

    lines = [
        f"整页按阅读顺序讲清以下 {len(ordered)} 段真实群聊剧情。序号仅表示阅读次序，不得画进图片。"
        "每段都要给出具体景别、人物动作、群友反应或道具特写，并使用下列逐字文字："
    ]
    for index, item in enumerate(ordered, start=1):
        title = _compact_visible_text(item.get("title"), 12) or "群聊话题"
        visible_people = item.get("visible_participants") if isinstance(item.get("visible_participants"), list) else []
        participant_label = next((str(value).strip() for value in visible_people if str(value).strip()), "")
        if not participant_label:
            participant_label = str(item.get("participant_label") or "群友（昵称未识别）").strip()
        fact = _compact_visible_text(item.get("summary"), 24) or "仅按真实消息证据绘制"
        quotes = item.get("quotes") if isinstance(item.get("quotes"), list) else []
        quote = next((_compact_visible_text(value, 22) for value in quotes if str(value or "").strip()), "")
        bubble = quote or _compact_visible_text(fact, 22)
        lines.append(
            f"{index}. 短标题逐字写《{title}》；完整姓名逐字写“{participant_label}”；"
            f"事实短句逐字写“{fact}”；主气泡逐字写“{bubble}”。"
            "画面指令必须补全景别、该人物的具体动作，以及群友反应或对应道具特写。"
        )
    lines.append(
        "以上每段指定文字在整张图中恰好出现一次。画面只显示允许的短标题、事实短句、完整姓名和气泡正文；"
        "不要显示序号、说明文字、字段名称、程序标识、英文装饰词、Logo、网址或额外标签。"
    )
    return "\n".join(lines)


class DeepSeekImagePromptBuilder:
    """保留历史类名以兼容现有注入点；默认实现已切换为 Codex GPT。"""

    name = "codex-gpt-image-prompt"

    def __init__(
        self,
        settings: Settings | None = None,
        templates: ImagePromptTemplateService | None = None,
        provider=None,
    ):
        self.settings = settings or get_settings()
        self.templates = templates or ImagePromptTemplateService()
        # V1/V2 使用同一主备 Provider，不重复实现模型调用。
        self._provider = provider or build_summary_provider(self.settings)

    # ---------- 对外 ----------

    def build(self, data: PromptInput) -> PromptOutput:
        started_at = perf_counter()
        api_model = self._provider.model
        try:
            theme = resolve_image_theme(
                data.image_theme,
                data.image_theme_custom,
                group_key=data.group_id or data.group_name,
                run_date=data.run_date,
                previous_signature=data.previous_theme_signature,
                persisted_meta=data.persisted_theme_meta,
            )
            template_source = data.template_override or self.templates.read(data.template)
            template_text = _strip_html_comments(template_source)
            validate_image_prompt_template(template_text)
            report_date = (data.report_date or data.period_end[:10]).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
                raise ValueError("report_date 必须来自统计周期并使用 YYYY-MM-DD")
            theme_text = f"{theme.display_name}：{theme.prompt}"
            date_line = f"统计日期：{report_date}"

            messages = [self._to_prompt_message(message, index) for index, message in enumerate(data.messages, start=1)]
            messages = [message for message in messages if message.text]
            direct_chars = max(1_000, int(self.settings.max_context_chars or 50_000))
            chunks = segment_messages(
                messages,
                direct_chars=direct_chars,
                target_chars=min(TARGET_CHUNK_CHARS, direct_chars),
                hard_chars=max(HARD_CHUNK_CHARS, direct_chars),
                session_gap_minutes=SESSION_GAP_MINUTES,
                overlap_messages=OVERLAP_MESSAGES,
            )
            if not chunks:
                raise ValueError("没有可提交给总结模型的聊天文本")
            meta: dict = {
                "template": data.template,
                "template_source": "group_override" if data.template_override else "global",
                "api_model": api_model,
                "primary_provider": self.settings.summary_provider_primary,
                "fallback_provider": self.settings.summary_provider_fallback,
                "message_lines": len(messages),
                "context_chars": sum(len(message.text) for message in messages),
                "chunk_count": len(chunks),
                "generated_at": datetime.now().isoformat(),
                "report_date": report_date,
            }
            meta.update(theme.to_meta())

            if len(chunks) <= 1:
                meta["mode"] = "direct"
                candidates, candidate_calls = self._topic_candidates_with_retry(
                    TOPIC_CANDIDATE_SYSTEM,
                    build_direct_candidate_prompt(chunks[0]),
                    chunks[0].message_ids,
                )
                analysis_calls = candidate_calls
            else:
                meta["mode"] = "natural_chunked"
                indexed_chunks = list(enumerate(chunks, start=1))

                def analyze(item: tuple[int, ConversationChunk]) -> tuple[list[dict], int]:
                    idx, chunk = item
                    return self._event_cards_with_retry(
                        EVENT_ANALYZE_SYSTEM + "\n\n" + self._theme_constraint(f"{theme.display_name}：{theme.prompt}"),
                        build_event_prompt(chunk, f"第 {idx}/{len(chunks)} 块"),
                        chunk,
                    )

                analysis_results = run_ai_tasks_ordered(
                    analyze,
                    indexed_chunks,
                    max_workers=normalized_limit(self.settings.ai_request_concurrency, 6),
                )
                analyses = [cards for cards, _ in analysis_results]
                event_calls = sum(calls for _, calls in analysis_results)
                cards = deduplicate_event_cards(analyses)
                if not cards:
                    raise ValueError("总结模型未从超长聊天中提取到可验证事件")
                meta["event_count"] = len(cards)
                candidates, candidate_calls = self._topic_candidates_with_retry(
                    TOPIC_CANDIDATE_SYSTEM,
                    build_merged_candidate_prompt(cards),
                    (message.message_id for message in messages),
                )
                analysis_calls = event_calls + candidate_calls

            selection = score_and_select_topics(candidates, messages)
            meta["topic_selection_version"] = selection["topic_selection_version"]
            meta["topic_selection"] = selection
            selected_payload = selected_topics_json(selection)
            topic_ids = selected_topic_ids(selection)
            recent_history = tuple(data.recent_layout_history or ())[:3]
            custom_style_text = theme.custom_text if theme.requested_key == "custom" else ""
            style_layout_locked = detect_explicit_style_layout(custom_style_text)
            preferred_layout = preferred_layout_from_style(custom_style_text)

            if preferred_layout:
                layout = fixed_layout_plan(
                    preferred_layout,
                    topic_ids,
                    recent_history=recent_history,
                )
                layout_calls = 0
            else:
                layout = restored_layout_plan(
                    data.persisted_theme_meta,
                    topic_ids,
                    style_layout_locked=style_layout_locked,
                )
                if layout is not None:
                    layout_calls = 0
                else:
                    layout, layout_calls = self._layout_plan_with_retry(
                        selected_payload,
                        topic_ids,
                        theme_text=theme_text,
                        recent_history=recent_history,
                        style_layout_locked=style_layout_locked,
                        seed_text=f"{data.group_id or data.group_name}|{data.run_date}",
                    )

            layout_instruction = resolved_layout_instruction(layout, custom_style_text)
            story_material = build_grounded_story_material(selection, layout.topic_order)
            meta.update(layout.to_meta())
            meta["recent_layout_ids"] = [
                str(item.get("layout_id") or "")
                for item in recent_history
                if isinstance(item, dict) and item.get("layout_id")
            ]

            structure = render_image_prompt_template(
                template_text,
                {
                    "group_name": data.group_name,
                    "period_start": data.period_start,
                    "period_end": data.period_end,
                    "report_date": report_date,
                    "message_count": str(data.message_count),
                    "speaker_count": str(data.speaker_count),
                    "image_theme": theme_text,
                    "layout_name": layout.layout_name,
                    "layout_instruction": layout_instruction,
                },
            )
            # 群级模板覆盖也必须服从真实剧情和漫画分镜契约。
            structure = f"{structure}\n\n{story_material}"
            if date_line not in structure:
                # 兼容没有新增占位符的旧/群级模板，同时保证每个最终 Prompt 都收到日期区块。
                structure = f"【固定画面日期】\n{date_line}\n\n{structure}"

            final_user_prompt = (
                "以下主题已经过原消息证据回查和喜剧优先固定评分。"
                "最终 Prompt 只能使用 selected_topics，并严格服从 storyboard_plan 的阅读顺序和逐话题镜头；"
                "不得加入未入选候选、临时改选、遗漏或重复主题；JSON 字段名和 topic ID 只供内部对应，"
                "绝对不要出现在最终 Prompt 或画面文字中：\n\n"
                + selected_payload
                + "\n\n【已校验漫画分镜方案】\n"
                + layout_plan_json(layout)
                + "\n\n"
                + story_material
            )
            text = ""
            final_calls = 0
            for attempt in range(FINAL_PROMPT_MAX_ATTEMPTS):
                prompt = final_user_prompt if attempt == 0 else final_user_prompt + _FINAL_PROMPT_RETRY_INSTRUCTION
                candidate_text = self._chat(
                    structure,
                    prompt,
                    theme_text,
                    layout_instruction,
                )
                final_calls += 1
                forbidden = [term for term in _FORBIDDEN_FINAL_PROMPT_TERMS if term in candidate_text]
                if forbidden:
                    logger.warning("最终 Prompt 暴露内部字段或主题 ID（第 %s/%s 次）：%s", attempt + 1, FINAL_PROMPT_MAX_ATTEMPTS, forbidden)
                    continue
                text = candidate_text
                break
            if not text:
                raise ValueError("最终生图 Prompt 连续暴露内部字段或主题 ID")

            mandatory_blocks: list[str] = []
            if story_material not in text:
                mandatory_blocks.append(story_material)
            if theme_text not in text:
                mandatory_blocks.append(
                    "【大主题】\n"
                    + theme_text
                    + "\n漫画分镜不得替换或削弱该指定风格。"
                )
            if layout.layout_name not in text:
                mandatory_blocks.append("【漫画分镜｜整张图只使用一种骨架】\n" + layout_instruction)
            if date_line not in text:
                mandatory_blocks.append(
                    "【必须在画面中清晰绘制的固定文字】\n"
                    + date_line
                    + "\n该日期标识必须位于海报顶部或底部，不得省略或改写。"
                )
            if mandatory_blocks:
                text = "\n\n".join((*mandatory_blocks, text))
            if date_line not in text:
                raise ValueError("最终生图 Prompt 缺少准确统计日期")
            forbidden = [term for term in _FORBIDDEN_FINAL_PROMPT_TERMS if term in text]
            if forbidden:
                raise ValueError("最终生图 Prompt 仍含内部字段或主题 ID：" + "、".join(forbidden))

            meta["api_call_count"] = analysis_calls + layout_calls + final_calls

            summary_ms = round((perf_counter() - started_at) * 1000)
            meta["summary_ms"] = summary_ms
            # 保留旧字段一版，避免历史运行分析与外部读取立即失效。
            meta["deepseek_ms"] = summary_ms
            return PromptOutput(success=True, prompt=text.strip(), model=api_model, meta=meta)
        except (ImagePromptTemplateError, ImageThemeError, LayoutPlanError, ValueError) as e:
            logger.warning("Prompt 模板错误：%s", e)
            return PromptOutput(success=False, error=str(e)[:300], model=api_model)
        except Exception as e:  # 主备模型调用失败等
            logger.exception("ImagePromptBuilder 生成失败")
            return PromptOutput(success=False, error=str(e)[:300], model=api_model)

    # ---------- 内部 ----------

    def _to_prompt_message(self, message, index: int) -> PromptMessage:
        timestamp = message.timestamp if hasattr(message.timestamp, "strftime") else None
        message_id = (
            getattr(message, "message_id", "")
            or getattr(message, "content_hash", "")
            or f"v2-{index}"
        )
        return PromptMessage(
            message_id=str(message_id),
            timestamp=timestamp,
            sender_name=message.sender_name or "(未知)",
            text=_to_ai_text(message).strip(),
            sender_id=str(getattr(message, "sender_id", "") or ""),
        )

    def _to_line(self, message) -> str:
        """兼容旧测试/诊断调用。"""
        item = self._to_prompt_message(message, 1)
        ts = item.timestamp.strftime("%H:%M") if item.timestamp else ""
        return f"[{ts}] {item.sender_name}: {item.text}"

    def _chunk(self, lines: list[str]) -> list[str]:
        """兼容旧调用；不再按消息条数切分。"""
        if not lines:
            return []
        messages = [PromptMessage(f"legacy-{index}", None, "", line) for index, line in enumerate(lines, start=1)]
        chunks = segment_messages(messages, direct_chars=max(1_000, int(self.settings.max_context_chars or 50_000)))
        return [chunk.text for chunk in chunks]

    @staticmethod
    def _theme_constraint(theme_prompt: str) -> str:
        return (
            "【大主题约束｜全图最高视觉约束】\n"
            + theme_prompt
            + "\n大主题控制全图配色、画材、服装、造型、装饰、纹理、光影和画风；"
            "不得创造、补充或改写聊天事实，也不得被整体版式替换或削弱。"
            "重新生图只允许改变所选美术家族及当天已解析的视觉细节，日期、数字、人物、逐字气泡、话题覆盖和既定分镜都是不变量。"
        )

    @staticmethod
    def _layout_constraint(layout_prompt: str) -> str:
        return (
            "【漫画分镜约束｜整张图只使用一种骨架】\n"
            + layout_prompt
            + "\n漫画分镜只控制格子几何、阅读路径和镜头节拍；必须服从大主题。"
        )

    def _chat(
        self,
        structure: str,
        user_prompt: str,
        theme_prompt: str = "",
        layout_prompt: str = "",
        *,
        response_format: str = "text",
        temperature: float = 0.7,
        max_tokens: int = 3000,
    ) -> str:
        """调用主备总结模型。system 含固定约束 + 模板输出结构。"""
        theme_block = "\n\n" + self._theme_constraint(theme_prompt) if theme_prompt else ""
        layout_block = "\n\n" + self._layout_constraint(layout_prompt) if layout_prompt else ""
        system = SYSTEM_BASE + theme_block + layout_block + "\n\n【输出结构】\n" + structure
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ]
        return self._provider._chat(
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _analysis_chat(
        self,
        system: str,
        user_prompt: str,
        *,
        response_format: str = "json_object",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> str:
        """结构化分析调用不混入最终海报格式约束，避免候选阶段角色冲突。"""
        return self._provider._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _layout_plan_with_retry(
        self,
        selected_topics_payload: str,
        topic_ids: list[str],
        *,
        theme_text: str,
        recent_history: tuple[dict, ...],
        style_layout_locked: bool,
        seed_text: str,
    ) -> tuple[LayoutPlan, int]:
        """版式 JSON 无效时完整重做一次，再使用不编造事实的确定性回退。"""
        history = tuple(item for item in recent_history if isinstance(item, dict))[:3]
        previous_layout_id = next(
            (
                str(item.get("layout_id") or "")
                for item in history
                if str(item.get("layout_id") or "") in IMAGE_LAYOUT_DEFINITIONS
            ),
            "",
        )
        user_prompt = build_layout_director_prompt(
            selected_topics_payload,
            theme_text=theme_text,
            recent_history=history,
            style_layout_locked=style_layout_locked,
        )
        last_error: LayoutPlanError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _LAYOUT_RETRY_INSTRUCTION
            raw = self._analysis_chat(
                LAYOUT_DIRECTOR_SYSTEM,
                prompt,
                response_format="json_object",
                temperature=0.4,
                max_tokens=LAYOUT_DIRECTOR_MAX_TOKENS,
            )
            try:
                return (
                    parse_layout_plan(
                        raw,
                        topic_ids,
                        previous_layout_id=previous_layout_id,
                        style_layout_locked=style_layout_locked,
                    ),
                    attempt + 1,
                )
            except LayoutPlanError as exc:
                last_error = exc
                logger.warning(
                    "版式导演结构化响应校验失败（第 %s/%s 次）：%s",
                    attempt + 1,
                    STRUCTURED_ANALYSIS_MAX_ATTEMPTS,
                    exc,
                )
        assert last_error is not None
        logger.warning("版式导演连续失败，使用确定性安全回退：%s", last_error)
        return (
            fallback_layout_plan(
                topic_ids,
                recent_history=history,
                seed_text=seed_text,
                style_layout_locked=style_layout_locked,
            ),
            STRUCTURED_ANALYSIS_MAX_ATTEMPTS,
        )

    def _topic_candidates_with_retry(
        self,
        system: str,
        user_prompt: str,
        allowed_message_ids,
    ) -> tuple[list[dict], int]:
        """格式或证据校验失败时完整重做一次，绝不接受截断 JSON。"""
        allowed_ids = tuple(allowed_message_ids)
        last_error: TopicSelectionError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _STRUCTURED_RETRY_INSTRUCTION
            raw = self._analysis_chat(
                system,
                prompt,
                response_format="json_object",
                temperature=0.1,
                max_tokens=TOPIC_CANDIDATE_MAX_TOKENS,
            )
            try:
                return parse_topic_candidates(raw, allowed_ids), attempt + 1
            except TopicSelectionError as exc:
                last_error = exc
                logger.warning("候选主题结构化响应校验失败（第 %s/%s 次）：%s", attempt + 1, STRUCTURED_ANALYSIS_MAX_ATTEMPTS, exc)
        assert last_error is not None
        raise last_error

    def _event_cards_with_retry(
        self,
        system: str,
        user_prompt: str,
        chunk: ConversationChunk,
    ) -> tuple[list[dict], int]:
        """片段事件 JSON 失败时完整重做一次，并返回真实模型调用次数。"""
        last_error: ValueError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _STRUCTURED_RETRY_INSTRUCTION
            raw = self._analysis_chat(
                system,
                prompt,
                response_format="json_object",
                temperature=0.1,
                max_tokens=EVENT_CARD_MAX_TOKENS,
            )
            try:
                return parse_event_cards(raw, chunk), attempt + 1
            except ValueError as exc:
                last_error = exc
                logger.warning("片段事件结构化响应校验失败（第 %s/%s 次）：%s", attempt + 1, STRUCTURED_ANALYSIS_MAX_ATTEMPTS, exc)
        assert last_error is not None
        raise last_error


# 新代码使用中性名称；历史导入继续可用，避免破坏已有测试和扩展注入点。
GroupSummaryImagePromptBuilder = DeepSeekImagePromptBuilder
