"""把证据化选题编辑为固定结构的微信群日报漫画 Prompt。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable

from app.ai.layouts import LayoutPlan, SHOT_LABELS


POSTER_COPY_VERSION = "fixed-chat-comic-v1"
MAX_VISIBLE_PARTICIPANTS = 4
MAX_VISIBLE_QUOTE_CHARS = 48
_GENERIC_COPY = ("信息量拉满", "一天顶一周", "比过山车还刺激")
_FORBIDDEN_RENDERED_TERMS = (
    "topic-",
    "message_id",
    "selected_topics",
    "storyboard_plan",
    "participant_options",
    "evidence_dialogue",
)
_ELLIPSIS_END_RE = re.compile(r"(?:\.{3}|…)$")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_GROUNDING_STOPWORDS = {
    "真实", "群友", "话题", "当天", "讨论", "聊天", "漫画", "事件", "画面",
    "继续", "开始", "一起", "现场", "今天", "日报", "群聊",
}
_CONFLICTING_RULES = (
    "不得绘制群名称",
    "不显示群名称",
    "不设置副标题",
    "不显示副标题",
    "不设置底部总结",
    "不显示底部总结",
    "每题只允许一个姓名",
    "每个话题只允许一个姓名",
    "每格只允许一个姓名",
)


class PosterCopyError(ValueError):
    """最终编辑稿不符合真实证据或固定结构。"""


@dataclass(frozen=True)
class ParticipantCopy:
    name: str
    action: str
    quote: str = ""


@dataclass(frozen=True)
class PanelCopy:
    topic_id: str
    title: str
    event_summary: str
    composition: str
    participants: tuple[ParticipantCopy, ...]
    visual_gag: str
    fact_line: str


@dataclass(frozen=True)
class DailyPosterCopy:
    title: str
    subtitle: str
    panels: tuple[PanelCopy, ...]
    footer_summary: str


def _clean_text(value: object, *, maximum: int, label: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise PosterCopyError(f"{label}不能为空")
    if len(text) > maximum:
        raise PosterCopyError(f"{label}超过 {maximum} 个字符")
    if _ELLIPSIS_END_RE.search(text):
        raise PosterCopyError(f"{label}不能以悬空省略号结尾")
    return text


def _selected_topics(selection: dict, topic_order: Iterable[str]) -> list[dict[str, Any]]:
    selected = [
        item
        for item in selection.get("candidates", [])
        if isinstance(item, dict) and item.get("selected")
    ]
    by_id = {str(item.get("topic_id") or ""): item for item in selected}
    ordered = [by_id[topic_id] for topic_id in topic_order if topic_id in by_id]
    if len(ordered) != len(selected):
        raise PosterCopyError("漫画阅读顺序没有覆盖全部入选主题")
    return ordered


def build_poster_editor_source(
    selection: dict,
    layout: LayoutPlan,
) -> dict[str, Any]:
    """生成仅供总结模型使用的证据编辑包；不会直接进入最终 Prompt。"""
    ordered = _selected_topics(selection, layout.topic_order)
    beats = {beat.topic_id: beat for beat in layout.panel_beats}
    topics: list[dict[str, Any]] = []
    for item in ordered:
        topic_id = str(item.get("topic_id") or "")
        visible = item.get("visible_participants")
        if not isinstance(visible, list):
            visible = []
        dialogue = item.get("evidence_dialogue")
        if not isinstance(dialogue, list):
            dialogue = []
        evidence_speakers = {
            str(entry.get("speaker") or "").strip()
            for entry in dialogue
            if isinstance(entry, dict) and str(entry.get("text") or "").strip()
        }
        participant_pool = [
            *visible,
            *(item.get("participants") or [] if isinstance(item.get("participants"), list) else []),
        ]
        participants: list[str] = []
        for value in participant_pool:
            name = str(value).strip()
            if not name or name in participants or name not in evidence_speakers:
                continue
            participants.append(name)
            if len(participants) >= MAX_VISIBLE_PARTICIPANTS:
                break
        evidence_dialogue = [
            {
                "message_id": str(entry.get("message_id") or ""),
                "speaker": str(entry.get("speaker") or "").strip(),
                "text": str(entry.get("text") or "").strip(),
            }
            for entry in dialogue
            if isinstance(entry, dict)
            and str(entry.get("speaker") or "").strip() in participants
            and str(entry.get("text") or "").strip()
        ]
        beat = beats.get(topic_id)
        topics.append(
            {
                "topic_id": topic_id,
                "source_title": str(item.get("title") or "").strip(),
                "source_summary": str(item.get("summary") or "").strip(),
                "source_visual_gag": str(item.get("visual_gag") or "").strip(),
                "participant_options": participants,
                "evidence_dialogue": evidence_dialogue,
                "shot_hints": [
                    SHOT_LABELS.get(shot, shot)
                    for shot in (beat.shots if beat is not None else ())
                ],
            }
        )
    return {
        "copy_version": POSTER_COPY_VERSION,
        "layout_hint": {
            "name": layout.layout_name,
            "structure_mode": layout.structure_mode,
            "comedy_device": layout.comedy_device,
            "reason": layout.layout_reason,
        },
        "topics": topics,
    }


POSTER_EDITOR_SYSTEM = """你是「群报 GroupBrief」的漫画日报内容编辑。
你只能根据给定的 evidence package 写一个 JSON 对象，不得输出 Markdown 或解释。
所有事实、姓名和对白必须来自对应 topic；不得改变金额、时间、地点和人物关系。
每个 panel 必须对应一个 topic_id，顺序不得改变。优先使用 2～4 位 participant_options；
每位人物都要写可绘制的动作、站位或反应。quote 只能逐字复制该 speaker 的 evidence_dialogue
完整原消息或其中连续、语义完整的片段，不得改写，不得用悬空省略号截断。
有两名以上真实发言人时，优先为至少两人各选一条能形成接话关系的真实气泡。
event_summary 与 fact_line 必须是完整句，只能概括 source_summary 和 evidence_dialogue。
视觉笑点只能字面化或放大已有内容，不得伪装成群里真实发生的新事件。
主标题、副标题和底部总结必须回收入选主题，不使用通用套话。
主标题不超过 24 个字符，副标题不超过 36 个字符，底部总结不超过 42 个字符；
每个版面标题不超过 18 个字符，事实说明不超过 72 个字符。写数字前必须确认该数字
原样存在于对应 topic 的 source_summary 或 evidence_dialogue 中。

严格 JSON 结构：
{
  "title": "当天主标题",
  "subtitle": "当天副标题",
  "panels": [
    {
      "topic_id": "原 topic_id",
      "title": "自然话题标题",
      "event_summary": "一至两句完整背景",
      "composition": "格子大小、景别、人物相对站位",
      "participants": [
        {"name": "真实昵称", "action": "动作、站位或反应", "quote": "可选真实原话"}
      ],
      "visual_gag": "不改变事实的视觉笑点",
      "fact_line": "完整简短事实说明"
    }
  ],
  "footer_summary": "当天底部总结"
}"""


def build_poster_editor_prompt(source: dict[str, Any]) -> str:
    return (
        "请把以下已证据校验的群聊主题内化成固定群聊漫画编辑稿。"
        "只返回 JSON；内部字段稍后由程序移除，不会交给图片模型。\n\n"
        + json.dumps(source, ensure_ascii=False, separators=(",", ":"))
    )


def _strip_json_fence(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _dialogue_by_speaker(topic: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for entry in topic.get("evidence_dialogue") or []:
        if not isinstance(entry, dict):
            continue
        speaker = str(entry.get("speaker") or "").strip()
        text = re.sub(r"\s+", " ", str(entry.get("text") or "")).strip()
        if speaker and text:
            result.setdefault(speaker, []).append(text)
    return result


def _quote_is_contiguous(quote: str, messages: Iterable[str]) -> bool:
    candidate = re.sub(r"\s+", " ", quote).strip().strip("“”\"")
    if not candidate:
        return False
    return any(candidate in message for message in messages)


def _assert_numbers_grounded(text: str, topic: dict[str, Any], label: str) -> None:
    evidence = " ".join(
        (
            str(topic.get("source_summary") or ""),
            *(str(item.get("text") or "") for item in topic.get("evidence_dialogue") or [] if isinstance(item, dict)),
        )
    )
    allowed = set(_NUMBER_RE.findall(evidence))
    invented = [number for number in _NUMBER_RE.findall(text) if number not in allowed]
    if invented:
        raise PosterCopyError(f"{label}包含证据中不存在的数字：{invented[0]}")


def _grounding_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    tokens.update(re.findall(r"[A-Za-z][A-Za-z0-9!~._-]+|\d+(?:\.\d+)?", text))
    return {token for token in tokens if token not in _GROUNDING_STOPWORDS}


def _assert_text_grounded(text: str, evidence: str, label: str) -> None:
    tokens = _grounding_tokens(text)
    if not tokens or not any(token in evidence for token in tokens):
        raise PosterCopyError(f"{label}没有回收到对应真实话题")


def parse_poster_copy(raw: str, source: dict[str, Any]) -> DailyPosterCopy:
    try:
        payload = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise PosterCopyError(f"漫画编辑稿不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise PosterCopyError("漫画编辑稿必须是 JSON 对象")

    title = _clean_text(payload.get("title"), maximum=24, label="主标题")
    subtitle = _clean_text(payload.get("subtitle"), maximum=36, label="副标题")
    footer = _clean_text(payload.get("footer_summary"), maximum=42, label="底部总结")
    if any(phrase in footer for phrase in _GENERIC_COPY):
        raise PosterCopyError("底部总结使用了禁用的通用套话")

    raw_panels = payload.get("panels")
    topics = source.get("topics")
    if not isinstance(raw_panels, list) or not isinstance(topics, list):
        raise PosterCopyError("漫画编辑稿缺少 panels")
    if len(raw_panels) != len(topics):
        raise PosterCopyError("版面数量与入选话题数量不一致")

    panels: list[PanelCopy] = []
    for index, (raw_panel, topic) in enumerate(zip(raw_panels, topics), start=1):
        if not isinstance(raw_panel, dict) or not isinstance(topic, dict):
            raise PosterCopyError(f"版面{index}格式无效")
        topic_id = str(raw_panel.get("topic_id") or "").strip()
        expected_topic_id = str(topic.get("topic_id") or "").strip()
        if topic_id != expected_topic_id:
            raise PosterCopyError(f"版面{index}未按既定话题顺序输出")

        panel_title = _clean_text(raw_panel.get("title"), maximum=18, label=f"版面{index}标题")
        event_summary = _clean_text(raw_panel.get("event_summary"), maximum=120, label=f"版面{index}背景")
        composition = _clean_text(raw_panel.get("composition"), maximum=160, label=f"版面{index}构图")
        visual_gag = _clean_text(raw_panel.get("visual_gag"), maximum=180, label=f"版面{index}视觉笑点")
        fact_line = _clean_text(raw_panel.get("fact_line"), maximum=72, label=f"版面{index}事实说明")
        _assert_numbers_grounded(event_summary, topic, f"版面{index}背景")
        _assert_numbers_grounded(fact_line, topic, f"版面{index}事实说明")
        topic_evidence = " ".join(
            (
                str(topic.get("source_title") or ""),
                str(topic.get("source_summary") or ""),
                " ".join(str(value) for value in topic.get("participant_options") or []),
                " ".join(
                    str(entry.get("text") or "")
                    for entry in topic.get("evidence_dialogue") or []
                    if isinstance(entry, dict)
                ),
            )
        )
        _assert_text_grounded(panel_title, topic_evidence, f"版面{index}标题")
        _assert_text_grounded(event_summary, topic_evidence, f"版面{index}背景")
        _assert_text_grounded(fact_line, topic_evidence, f"版面{index}事实说明")

        participant_options = [str(value).strip() for value in topic.get("participant_options") or [] if str(value).strip()]
        dialogue = _dialogue_by_speaker(topic)
        raw_participants = raw_panel.get("participants")
        if not isinstance(raw_participants, list):
            raise PosterCopyError(f"版面{index}缺少 participants")
        minimum = 2 if len(participant_options) >= 2 else 1
        if not (minimum <= len(raw_participants) <= min(MAX_VISIBLE_PARTICIPANTS, len(participant_options))):
            raise PosterCopyError(f"版面{index}应使用 {minimum}～{min(MAX_VISIBLE_PARTICIPANTS, len(participant_options))} 位真实参与者")

        participants: list[ParticipantCopy] = []
        names: set[str] = set()
        quoted_speakers: set[str] = set()
        for person_index, raw_person in enumerate(raw_participants, start=1):
            if not isinstance(raw_person, dict):
                raise PosterCopyError(f"版面{index}第{person_index}位人物格式无效")
            name = str(raw_person.get("name") or "").strip()
            if name not in participant_options or name in names:
                raise PosterCopyError(f"版面{index}包含未授权或重复的群友姓名：{name or '空'}")
            names.add(name)
            action = _clean_text(raw_person.get("action"), maximum=120, label=f"版面{index}人物动作")
            quote = re.sub(r"\s+", " ", str(raw_person.get("quote") or "")).strip().strip("“”\"")
            if quote:
                quote = _clean_text(quote, maximum=MAX_VISIBLE_QUOTE_CHARS, label=f"版面{index}真实气泡")
                if not _quote_is_contiguous(quote, dialogue.get(name, [])):
                    raise PosterCopyError(f"版面{index}中“{name}”的气泡无法回查原消息")
                quoted_speakers.add(name)
            participants.append(ParticipantCopy(name=name, action=action, quote=quote))

        available_quoted_speakers = {name for name in participant_options if dialogue.get(name)}
        required_quotes = 2 if len(available_quoted_speakers) >= 2 and len(participants) >= 2 else 1
        if len(quoted_speakers) < required_quotes:
            raise PosterCopyError(f"版面{index}缺少足够的真实多人对白")

        panels.append(
            PanelCopy(
                topic_id=topic_id,
                title=panel_title,
                event_summary=event_summary,
                composition=composition,
                participants=tuple(participants),
                visual_gag=visual_gag,
                fact_line=fact_line,
            )
        )
    all_evidence = " ".join(
        " ".join(
            (
                str(topic.get("source_title") or ""),
                str(topic.get("source_summary") or ""),
                " ".join(str(value) for value in topic.get("participant_options") or []),
                " ".join(
                    str(entry.get("text") or "")
                    for entry in topic.get("evidence_dialogue") or []
                    if isinstance(entry, dict)
                ),
            )
        )
        for topic in topics
        if isinstance(topic, dict)
    )
    _assert_text_grounded(title, all_evidence, "主标题")
    _assert_text_grounded(subtitle, all_evidence, "副标题")
    _assert_text_grounded(footer, all_evidence, "底部总结")
    return DailyPosterCopy(title=title, subtitle=subtitle, panels=tuple(panels), footer_summary=footer)


def _overall_visual(style_text: str, *, explicit_style: bool) -> str:
    style_line = (
        f"本次手动视觉风格：{style_text}"
        if explicit_style
        else "根据当天真实聊天内容自由选择统一视觉风格。"
    )
    return "\n\n".join(
        (
            "生成一张适合微信手机端阅读的 1024×1536 竖版漫画群报，原生画布比例严格为 2:3；不得生成 9:19 超长手机截图比例，也不得生成 864×1821 画布。",
            style_line,
            "整张图像一页热闹的群聊漫画：顶部是群名称、完整统计时间、主标题和副标题，中间由多个大小错落的话题漫画格组成，底部展示当天总结和统计数据。",
            "每个话题都要画成一个真实的“群友讨论现场”，而不是单人物插画。每个话题优先选择 2～4 位真正参与该段聊天的群友出镜，人物旁边直接标注对应的真实群昵称。",
            "不同群友使用不同动作、表情和站位，以真实聊天气泡、人物反应、道具、动作线和视觉笑点表现讨论过程。所有剧情、人物关系和聊天内容均来自当天真实群聊，不额外编造新的聊天事实。",
        )
    )


def _render_panel(index: int, panel: PanelCopy) -> str:
    paragraphs = [f"【版面{index}】", panel.title, panel.event_summary, panel.composition]
    for participant in panel.participants:
        paragraphs.append(
            f"{participant.name}{participant.action}。人物旁清晰标注“{participant.name}”。"
        )
        if participant.quote:
            paragraphs.append(f"该群友说：\n\n“{participant.quote}”")
    paragraphs.extend(
        (
            panel.visual_gag,
            "版面中自然加入一句简短事实说明：\n\n" + panel.fact_line,
        )
    )
    return "\n\n".join(paragraphs)


TEXT_RULES = """图片中允许出现：

群名称、完整统计时间、主标题、副标题、统计数据、每个话题标题、真实群友昵称、精选真实聊天气泡、简短事实说明和底部总结。

群友昵称应贴近对应人物显示，让读者能够直接识别每个人是谁。同一个话题可以显示多位真实群友姓名；优先展示真正参与该段聊天的人，不为了凑人数添加无关群友。

海报顶部的群名称、完整统计时间、主标题和副标题，以及底部总结、消息数和发言人数均不可省略、改写或缩小至不可读。

不要把“任务”“版面”“画面”“人物”“参与者”“事实说明”“文字规则”“主气泡”“代表人物”“可用文字”等 Prompt 结构性字段画进图片。

不要额外生成程序字段、序号、英文装饰词、Logo、网址、无关品牌或与聊天无关的文字。真实聊天中必要的产品名可作为普通文字保留，但不绘制品牌 Logo。

空间不足时先减少装饰、道具和次要反应，不删除顶部、底部、真实姓名和主要对白。"""


def render_poster_prompt(
    copy: DailyPosterCopy,
    *,
    group_name: str,
    period_line: str,
    message_line: str,
    speaker_line: str,
    style_text: str,
    explicit_style: bool,
    template_text: str = "",
) -> str:
    panels = "\n\n".join(
        _render_panel(index, panel) for index, panel in enumerate(copy.panels, start=1)
    )
    overall_visual = _overall_visual(style_text, explicit_style=explicit_style)
    if template_text:
        from app.ai.prompt_templates import render_image_prompt_template

        period_start, separator, period_end = period_line.partition(" ~ ")
        if not separator:
            raise PosterCopyError("完整统计时间格式无效")
        message_count = message_line.removesuffix(" 条消息")
        speaker_count = speaker_line.removesuffix(" 人发言")
        prompt = render_image_prompt_template(
            template_text,
            {
                "group_name": group_name,
                "period_start": period_start,
                "period_end": period_end,
                "message_count": message_count,
                "speaker_count": speaker_count,
                "main_title": copy.title,
                "subtitle": copy.subtitle,
                "overall_visual": overall_visual,
                "panels": panels,
                "text_rules": TEXT_RULES,
                "footer_summary": copy.footer_summary,
            },
        ).strip()
    else:
        parts = [
            "【任务】\n\n生成一张竖版微信群日报漫画信息图。",
            f"【群名称】\n\n{group_name}",
            f"【统计时间】\n\n{period_line}",
            f"【数据】\n\n{message_line}\n{speaker_line}",
            f"【主标题】\n\n{copy.title}",
            f"【副标题】\n\n{copy.subtitle}",
            "【整体视觉】\n\n" + overall_visual,
            panels,
            "【文字规则】\n\n" + TEXT_RULES,
            f"【底部总结】\n\n{copy.footer_summary}",
        ]
        prompt = "\n\n".join(parts).strip()
    validate_rendered_poster_prompt(
        prompt,
        copy=copy,
        group_name=group_name,
        period_line=period_line,
        message_line=message_line,
        speaker_line=speaker_line,
    )
    return prompt


def _section_headings(prompt: str) -> list[str]:
    return re.findall(r"(?m)^【([^\n】]+)】\s*$", prompt)


def validate_fixed_prompt_contract(
    prompt: str,
    *,
    expected_panel_count: int | None = None,
) -> int:
    """校验人工编辑后的固定合同；不依赖内部编辑 JSON。"""
    headings = _section_headings(prompt)
    panel_headings = [heading for heading in headings if re.fullmatch(r"版面\d+", heading)]
    panel_count = len(panel_headings)
    if not 2 <= panel_count <= 7:
        raise PosterCopyError("最终 Prompt 必须包含连续的 2～7 个版面")
    if expected_panel_count is not None and panel_count != expected_panel_count:
        raise PosterCopyError("版面数量与本次已校验入选话题数量不一致")
    expected = [
        "任务",
        "群名称",
        "统计时间",
        "数据",
        "主标题",
        "副标题",
        "整体视觉",
        *(f"版面{index}" for index in range(1, panel_count + 1)),
        "文字规则",
        "底部总结",
    ]
    if headings != expected:
        raise PosterCopyError("最终 Prompt 区块名称、顺序或版面编号不符合固定合同")

    sections = re.split(r"(?m)^【[^\n】]+】\s*$", prompt)[1:]
    if len(sections) != len(headings):
        raise PosterCopyError("最终 Prompt 区块解析失败")
    for heading, body in zip(headings, sections):
        if not body.strip():
            raise PosterCopyError(f"【{heading}】缺少真实内容")

    if not re.search(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s*~\s*"
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
        prompt,
    ):
        raise PosterCopyError("最终 Prompt 缺少完整统计开始和结束时间")
    if not re.search(r"\d+\s*条消息", prompt) or not re.search(r"\d+\s*人发言", prompt):
        raise PosterCopyError("最终 Prompt 缺少消息数或发言人数")
    for phrase in _CONFLICTING_RULES:
        if phrase in prompt:
            raise PosterCopyError(f"最终 Prompt 包含冲突规则：{phrase}")
    for forbidden in _FORBIDDEN_RENDERED_TERMS:
        if forbidden in prompt:
            raise PosterCopyError(f"最终 Prompt 暴露内部字段：{forbidden}")
    return panel_count


def validate_rendered_poster_prompt(
    prompt: str,
    *,
    copy: DailyPosterCopy,
    group_name: str,
    period_line: str,
    message_line: str,
    speaker_line: str,
) -> None:
    validate_fixed_prompt_contract(prompt, expected_panel_count=len(copy.panels))
    expected = [
        "任务",
        "群名称",
        "统计时间",
        "数据",
        "主标题",
        "副标题",
        "整体视觉",
        *(f"版面{index}" for index in range(1, len(copy.panels) + 1)),
        "文字规则",
        "底部总结",
    ]
    headings = _section_headings(prompt)
    if headings != expected:
        raise PosterCopyError("最终 Prompt 区块名称、顺序或版面编号不符合固定合同")
    for literal, label in (
        (group_name, "群名称"),
        (period_line, "完整统计时间"),
        (message_line, "消息数"),
        (speaker_line, "发言人数"),
        (copy.title, "主标题"),
        (copy.subtitle, "副标题"),
        (copy.footer_summary, "底部总结"),
    ):
        if literal not in prompt:
            raise PosterCopyError(f"最终 Prompt 缺少{label}")
    for forbidden in _FORBIDDEN_RENDERED_TERMS:
        if forbidden in prompt:
            raise PosterCopyError(f"最终 Prompt 暴露内部字段：{forbidden}")
    for panel in copy.panels:
        for participant in panel.participants:
            if participant.name not in prompt:
                raise PosterCopyError(f"最终 Prompt 缺少群友姓名：{participant.name}")
            if participant.quote and participant.quote not in prompt:
                raise PosterCopyError(f"最终 Prompt 缺少真实气泡：{participant.quote}")
        if panel.fact_line not in prompt:
            raise PosterCopyError(f"最终 Prompt 缺少事实说明：{panel.fact_line}")
