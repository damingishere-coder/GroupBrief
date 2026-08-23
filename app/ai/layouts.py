"""整张日报的漫画分镜骨架、镜头节拍与安全回退。

版式只控制格子尺寸、阅读路径、镜头节奏和跨格关系。配色、画材、
人物造型、纹理与光影始终由 image_theme / image_theme_custom 控制。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


LAYOUT_CATALOG_VERSION = "comic-panels-v3"

STRUCTURE_MODES: tuple[str, ...] = (
    "hero_rhythm",
    "dual_rhythm",
    "ensemble_rhythm",
)

STRUCTURE_MODE_LABELS = {
    "hero_rhythm": "头条带动",
    "dual_rhythm": "双焦点交替",
    "ensemble_rhythm": "群像错落",
}

SHOT_TYPES: tuple[str, ...] = (
    "establishing",
    "action",
    "dialogue",
    "reaction",
    "close_up",
    "punchline",
    "insert",
)

SHOT_LABELS = {
    "establishing": "环境建立镜头",
    "action": "动作镜头",
    "dialogue": "对白镜头",
    "reaction": "反应镜头",
    "close_up": "局部特写",
    "punchline": "包袱落点",
    "insert": "道具或聊天细节插入镜头",
}

COMEDY_DEVICES: tuple[str, ...] = (
    "字面化",
    "反差",
    "回环",
    "误会与反转",
    "一本正经地荒诞",
)


class LayoutPlanError(ValueError):
    """分镜导演返回了无法安全使用的结构。"""


@dataclass(frozen=True)
class ImageLayoutDefinition:
    key: str
    label: str
    best_for: str
    size_signature: str
    reading_path: str
    instruction: str


@dataclass(frozen=True)
class PanelBeat:
    topic_id: str
    shots: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"topic_id": self.topic_id, "shots": list(self.shots)}


@dataclass(frozen=True)
class LayoutPlan:
    layout_id: str
    layout_name: str
    structure_mode: str
    featured_topic_ids: tuple[str, ...]
    topic_order: tuple[str, ...]
    panel_beats: tuple[PanelBeat, ...]
    comedy_device: str
    layout_reason: str
    style_layout_locked: bool = False
    reused: bool = False

    @property
    def panel_count(self) -> int:
        return sum(len(beat.shots) for beat in self.panel_beats)

    @property
    def signature(self) -> str:
        definition = IMAGE_LAYOUT_DEFINITIONS[self.layout_id]
        beat_material = tuple(
            f"{beat.topic_id}:{','.join(beat.shots)}" for beat in self.panel_beats
        )
        material = "|".join(
            (
                LAYOUT_CATALOG_VERSION,
                self.layout_id,
                definition.size_signature,
                self.structure_mode,
                *self.featured_topic_ids,
                *self.topic_order,
                *beat_material,
                self.comedy_device,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def to_meta(self) -> dict[str, Any]:
        definition = IMAGE_LAYOUT_DEFINITIONS[self.layout_id]
        return {
            "layout_catalog_version": LAYOUT_CATALOG_VERSION,
            "layout_id": self.layout_id,
            "layout_name": self.layout_name,
            "layout_signature": self.signature,
            "panel_size_signature": definition.size_signature,
            "reading_path": definition.reading_path,
            "structure_mode": self.structure_mode,
            "featured_topic_ids": list(self.featured_topic_ids),
            "topic_order": list(self.topic_order),
            "panel_beats": [beat.to_dict() for beat in self.panel_beats],
            "panel_count": self.panel_count,
            "comedy_device": self.comedy_device,
            "layout_reason": self.layout_reason,
            "style_layout_locked": self.style_layout_locked,
            "layout_reused": self.reused,
        }


IMAGE_LAYOUT_DEFINITIONS: dict[str, ImageLayoutDefinition] = {
    "hero_with_insets": ImageLayoutDefinition(
        "hero_with_insets",
        "头条大格与插格",
        "有一个最适合展开动作和群友反应的主梗",
        "XL-L-M-S-S-INSET-HERO",
        "顶部大格起势，沿大格边缘插入反应特写，再向下错落阅读",
        "内容区先用约 35% 的不规则大格建立主场景；大格内部或边缘嵌 1～2 个反应/道具小格，"
        "其余话题使用宽格、竖格和小格交错承接。允许人物、对白尾巴或关键道具越过一条格线，"
        "但文字不得被遮挡；严禁把各话题排成等大圆角模块。",
    ),
    "staggered_mosaic": ImageLayoutDefinition(
        "staggered_mosaic",
        "错落马赛克分镜",
        "多个独立热点，需要密集但不列表化",
        "L-TALL-M-WIDE-S-S",
        "从左上到右下折线阅读，宽格与竖格交替",
        "使用至少三种明显不同的格子尺寸：一块宽格、一块窄竖格、两块中格及若干小反应格；"
        "相邻边线错位，局部可嵌套，但保留清晰沟槽和折线阅读顺序。不得形成整齐的两列等高模块。",
    ),
    "cinematic_strips": ImageLayoutDefinition(
        "cinematic_strips",
        "电影条带连续镜头",
        "对话有前后动作、反转或时间推进",
        "XL-WIDE-WIDE-M-S",
        "全宽开场后纵向推进，结尾以特写或包袱格收束",
        "顶部用一块全宽远景或动作格开场；中段以不同高度的横向条带和局部左右切分推进；"
        "至少一组同话题连续镜头表现起因—反应或动作—包袱，结尾使用小特写格，不得做成独立模块清单。",
    ),
    "nested_reactions": ImageLayoutDefinition(
        "nested_reactions",
        "大场景嵌套反应格",
        "群友表情和接话比事件说明更好玩",
        "L[S+S]-L[S]-M-S",
        "先读大场景，再读嵌在其中的表情特写，随后跳到下一场景",
        "安排 2～3 个较大的场景格，每个大格可内嵌一个头像反应、聊天气泡或道具特写小格；"
        "剩余话题以不等高小格穿插。嵌套格要像漫画反应镜头，不得变成字段标签或数据卡。",
    ),
    "diagonal_burst": ImageLayoutDefinition(
        "diagonal_burst",
        "对角爆发跨格",
        "动作感、夸张道具或一句话引发连锁反应",
        "XL-DIAGONAL-M-S-S-CROSS",
        "沿左上到右下的对角动作线阅读，再回看两侧反应格",
        "用一条明显对角线切开主动作格，主角、道具或效果线可跨越相邻格边；"
        "两侧布置尺寸不等的补充场景与反应特写，气泡尾巴引导阅读方向。跨格只增强同一真实笑点，"
        "不能把无关话题画成虚构因果。",
    ),
    "sidecar_scroll": ImageLayoutDefinition(
        "sidecar_scroll",
        "纵向主卷与侧挂格",
        "有一条长对话/过程，同时并行多个短热点",
        "TALL-L-M-S-S-INSET",
        "沿窄长主卷向下阅读，左右侧挂格交替接入",
        "一条窄长主分镜贯穿内容区约三分之二高度，表现一个话题的连续动作或对话；"
        "其余话题以左右交替的宽格、方格和贴边反应小格挂接。主卷与侧格可用气泡尾巴互相呼应，"
        "不得排成统一宽度的纵向列表。",
    ),
    "split_focus": ImageLayoutDefinition(
        "split_focus",
        "双焦点不对称分屏",
        "两个强话题形成对照，其余话题负责反应或补充",
        "XL-L-M-S-S-INSET",
        "先读左上大格，再读右侧次大格，随后蛇形扫过支援格",
        "设置面积不同的两个焦点场景，禁止机械五五分；在两者之间或内部嵌入对照特写，"
        "下方用高低错落的小格承载其余话题。可让同一反应人物探出格线连接两边，但不虚构对话关系。",
    ),
    "freeform_collage": ImageLayoutDefinition(
        "freeform_collage",
        "自由漫画拼贴",
        "一天话题类型跨度大，但仍需要明确阅读节奏",
        "L-M-S-INSET-OVERLAP-CROSS",
        "由最大气泡或动作线起读，按编号很小的视觉线索绕页推进",
        "采用不规则漫画页：切角格、圆形特写、贴纸式反应格和一处轻微重叠；至少三种尺寸，"
        "允许一个人物或道具跨两格，但每个话题边界仍可辨。阅读线索由气泡尾巴、视线和动作线完成，"
        "不要用表格、仪表盘或等大矩形模块组织内容。",
    ),
}

IMAGE_LAYOUT_KEYS = tuple(IMAGE_LAYOUT_DEFINITIONS)


LAYOUT_DIRECTOR_SYSTEM = """你是 GroupBrief 的漫画分镜导演。
只能使用给定的已入选主题，必须返回一个 JSON 对象，不得输出 Markdown 或解释。

你设计的是一整页漫画内部的格子节奏，不是法庭、菜单、地图、新闻台等主题场景。
一个话题不等于一个矩形模块：每个话题分配 1～3 个镜头；至少一个话题必须用连续两个以上镜头；
总镜头数必须为话题数 + 1 到 min(话题数 + 4, 12)。镜头类型只能是 establishing、action、
dialogue、reaction、close_up、punchline、insert。全部话题必须恰好出现一次于 panel_beats，
topic_order 也必须恰好覆盖全部话题。

根据内容选择 hero_rhythm、dual_rhythm 或 ensemble_rhythm。无论哪种模式，都必须保留明显的
大/中/小格层级，禁止等宽等高列表。指定风格只控制配色、画材、造型、纹理和光影；版式不能改写画风。
优先选择最符合笑点节奏的分镜骨架；不得选择前一次使用的骨架，并尽量避开最近三次。"""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_EXPLICIT_LAYOUT_HINTS = (
    "版式", "布局", "构图", "四格", "连环画", "漫画分镜", "分镜",
    "三栏", "分栏", "头版", "封面", "大小格", "嵌套格", "跨格", "条带",
)

_EXPLICIT_LAYOUT_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("四格", "连环画", "条带"), "cinematic_strips"),
    (("嵌套格",), "nested_reactions"),
    (("跨格", "对角"), "diagonal_burst"),
    (("三栏", "分栏"), "staggered_mosaic"),
    (("双焦点", "分屏"), "split_focus"),
    (("头版", "封面", "大格"), "hero_with_insets"),
    (("拼贴", "自由分镜"), "freeform_collage"),
)


def detect_explicit_style_layout(custom_text: str) -> bool:
    """保守识别用户自定义风格中明确写出的漫画版式要求。"""
    text = (custom_text or "").strip()
    return bool(text and any(hint in text for hint in _EXPLICIT_LAYOUT_HINTS))


def preferred_layout_from_style(custom_text: str) -> str:
    """将可明确映射的用户分镜词转换为骨架 ID。"""
    text = (custom_text or "").strip()
    for hints, layout_id in _EXPLICIT_LAYOUT_MAP:
        if any(hint in text for hint in hints):
            return layout_id
    return ""


def selected_topic_ids(selection: dict[str, Any]) -> list[str]:
    return [
        str(item.get("topic_id") or "")
        for item in selection.get("candidates", [])
        if item.get("selected") and item.get("topic_id")
    ]


def _history_layout_ids(history: Iterable[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        layout_id = str(item.get("layout_id") or "")
        if layout_id in IMAGE_LAYOUT_DEFINITIONS and layout_id not in result:
            result.append(layout_id)
    return result


def _history_comedy_devices(history: Iterable[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        device = str(item.get("comedy_device") or "")
        if device in COMEDY_DEVICES and device not in result:
            result.append(device)
    return result


def build_layout_director_prompt(
    selected_topics_payload: str,
    *,
    theme_text: str,
    recent_history: Iterable[dict[str, Any]],
    style_layout_locked: bool,
) -> str:
    history = [item for item in recent_history if isinstance(item, dict)][:3]
    catalog = [
        {
            "layout_id": item.key,
            "name": item.label,
            "best_for": item.best_for,
            "size_signature": item.size_signature,
            "reading_path": item.reading_path,
        }
        for item in IMAGE_LAYOUT_DEFINITIONS.values()
    ]
    return (
        "请为这组已校验主题选择唯一漫画分镜骨架，并设计逐话题镜头节拍。\n\n"
        "返回结构：\n"
        '{"layout_id":"8种ID之一",'
        '"structure_mode":"hero_rhythm/dual_rhythm/ensemble_rhythm",'
        '"featured_topic_ids":["头条1个/双焦点2个/群像为空"],'
        '"topic_order":["全部入选ID，恰好一次"],'
        '"panel_beats":[{"topic_id":"入选ID","shots":["1～3个合法镜头类型"]}],'
        '"comedy_device":"字面化/反差/回环/误会与反转/一本正经地荒诞",'
        '"layout_reason":"简短理由"}\n\n'
        f"指定风格（只能服从，不得改写）：{theme_text}\n"
        f"指定风格是否含明确分镜要求：{str(style_layout_locked).lower()}\n"
        f"最近分镜历史：{json.dumps(history, ensure_ascii=False, separators=(',', ':'))}\n"
        f"可选分镜骨架：{json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))}\n"
        f"已入选主题：{selected_topics_payload}"
    )


def _expected_topic_ids(topic_ids: Iterable[str]) -> list[str]:
    expected = [str(topic_id) for topic_id in topic_ids if str(topic_id)]
    if not (2 <= len(expected) <= 7) or len(set(expected)) != len(expected):
        raise LayoutPlanError("入选主题必须是 2～7 个不重复 ID")
    return expected


def _parse_panel_beats(raw: Any, expected: list[str]) -> tuple[PanelBeat, ...]:
    if not isinstance(raw, list):
        raise LayoutPlanError("panel_beats 必须是数组")
    beats: list[PanelBeat] = []
    for item in raw:
        if not isinstance(item, dict):
            raise LayoutPlanError("panel_beats 每项必须是对象")
        topic_id = str(item.get("topic_id") or "").strip()
        raw_shots = item.get("shots")
        if topic_id not in expected or not isinstance(raw_shots, list):
            raise LayoutPlanError("panel_beats 只能引用入选主题并提供 shots 数组")
        shots = tuple(str(shot).strip() for shot in raw_shots if str(shot).strip())
        if not (1 <= len(shots) <= 3) or any(shot not in SHOT_TYPES for shot in shots):
            raise LayoutPlanError("每个话题必须包含 1～3 个合法镜头类型")
        beats.append(PanelBeat(topic_id, shots))
    if len(beats) != len(expected) or {beat.topic_id for beat in beats} != set(expected):
        raise LayoutPlanError("panel_beats 必须恰好覆盖全部入选主题且每个主题只出现一次")
    panel_count = sum(len(beat.shots) for beat in beats)
    maximum = min(len(expected) + 4, 12)
    if not (len(expected) + 1 <= panel_count <= maximum):
        raise LayoutPlanError(f"总镜头数必须在 {len(expected) + 1}～{maximum} 之间")
    return tuple(beats)


def _featured_topics(payload: dict[str, Any], expected: list[str], mode: str) -> tuple[str, ...]:
    raw_featured = payload.get("featured_topic_ids")
    if not isinstance(raw_featured, list):
        raise LayoutPlanError("featured_topic_ids 必须是数组")
    featured = tuple(item.strip() for item in raw_featured if isinstance(item, str) and item.strip())
    if len(featured) != len(set(featured)) or any(item not in expected for item in featured):
        raise LayoutPlanError("重点话题必须是无重复的入选主题")
    expected_count = {"hero_rhythm": 1, "dual_rhythm": 2, "ensemble_rhythm": 0}[mode]
    if len(featured) != expected_count:
        raise LayoutPlanError(f"{mode} 必须包含 {expected_count} 个重点话题")
    return featured


def parse_layout_plan(
    raw: str,
    topic_ids: Iterable[str],
    *,
    previous_layout_id: str = "",
    style_layout_locked: bool = False,
) -> LayoutPlan:
    cleaned = _FENCE_RE.sub("", (raw or "").strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LayoutPlanError(f"分镜导演响应不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LayoutPlanError("分镜导演响应必须是 JSON 对象")

    expected = _expected_topic_ids(topic_ids)
    layout_id = str(payload.get("layout_id") or "").strip()
    if layout_id not in IMAGE_LAYOUT_DEFINITIONS:
        raise LayoutPlanError(f"未知漫画分镜：{layout_id!r}")
    if previous_layout_id and layout_id == previous_layout_id and not style_layout_locked:
        raise LayoutPlanError("漫画分镜不得与前一次连续重复")

    structure_mode = str(payload.get("structure_mode") or "").strip()
    if structure_mode not in STRUCTURE_MODES:
        raise LayoutPlanError(f"未知内容节奏：{structure_mode!r}")
    featured = _featured_topics(payload, expected, structure_mode)

    raw_order = payload.get("topic_order")
    if not isinstance(raw_order, list):
        raise LayoutPlanError("topic_order 必须是数组")
    topic_order = tuple(item.strip() for item in raw_order if isinstance(item, str) and item.strip())
    if len(topic_order) != len(set(topic_order)) or set(topic_order) != set(expected):
        raise LayoutPlanError("topic_order 必须恰好覆盖全部入选主题且不得重复")
    beats = _parse_panel_beats(payload.get("panel_beats"), expected)

    comedy_device = str(payload.get("comedy_device") or "").strip()
    if comedy_device not in COMEDY_DEVICES:
        raise LayoutPlanError(f"未知喜剧机制：{comedy_device!r}")
    reason = str(payload.get("layout_reason") or "").strip()[:240] or "按真实对话节奏选择漫画分镜"
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        structure_mode,
        featured,
        topic_order,
        beats,
        comedy_device,
        reason,
        style_layout_locked=style_layout_locked,
    )


def restored_layout_plan(
    persisted_meta: dict[str, Any] | None,
    topic_ids: Iterable[str],
    *,
    style_layout_locked: bool,
) -> LayoutPlan | None:
    if not isinstance(persisted_meta, dict):
        return None
    if persisted_meta.get("layout_catalog_version") != LAYOUT_CATALOG_VERSION:
        return None
    try:
        expected = _expected_topic_ids(topic_ids)
        layout_id = str(persisted_meta.get("layout_id") or "")
        if layout_id not in IMAGE_LAYOUT_DEFINITIONS:
            return None
        structure_mode = str(persisted_meta.get("structure_mode") or "")
        if structure_mode not in STRUCTURE_MODES:
            return None
        featured = _featured_topics(persisted_meta, expected, structure_mode)
        raw_order = persisted_meta.get("topic_order")
        if not isinstance(raw_order, list):
            return None
        topic_order = tuple(str(item).strip() for item in raw_order if str(item).strip())
        if len(topic_order) != len(set(topic_order)) or set(topic_order) != set(expected):
            return None
        beats = _parse_panel_beats(persisted_meta.get("panel_beats"), expected)
    except LayoutPlanError:
        return None
    comedy_device = str(persisted_meta.get("comedy_device") or "")
    if comedy_device not in COMEDY_DEVICES:
        comedy_device = COMEDY_DEVICES[0]
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        structure_mode,
        featured,
        topic_order,
        beats,
        comedy_device,
        str(persisted_meta.get("layout_reason") or "同群同日复用已选分镜")[:240],
        style_layout_locked=style_layout_locked,
        reused=True,
    )


def _fallback_beats(expected: list[str]) -> tuple[PanelBeat, ...]:
    shot_cycle = ("dialogue", "action", "reaction", "close_up", "punchline", "insert", "establishing")
    beats: list[PanelBeat] = []
    for index, topic_id in enumerate(expected):
        shots = (shot_cycle[index % len(shot_cycle)],)
        if index == 0:
            shots = ("establishing", "punchline")
        beats.append(PanelBeat(topic_id, shots))
    return tuple(beats)


def fixed_layout_plan(
    layout_id: str,
    topic_ids: Iterable[str],
    *,
    recent_history: Iterable[dict[str, Any]] = (),
) -> LayoutPlan:
    """用户自定义风格明确指定分镜时，直接服从该结构。"""
    expected = _expected_topic_ids(topic_ids)
    if layout_id not in IMAGE_LAYOUT_DEFINITIONS:
        raise LayoutPlanError("无法应用指定风格中的漫画分镜")
    avoided_devices = set(_history_comedy_devices(recent_history)[:2])
    device = next((item for item in COMEDY_DEVICES if item not in avoided_devices), COMEDY_DEVICES[0])
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    mode = "dual_rhythm" if len(expected) == 2 else "hero_rhythm"
    featured = tuple(expected[:2]) if mode == "dual_rhythm" else (expected[0],)
    return LayoutPlan(
        layout_id,
        definition.label,
        mode,
        featured,
        tuple(expected),
        _fallback_beats(expected),
        device,
        "用户指定风格中包含明确漫画分镜要求",
        style_layout_locked=True,
    )


def fallback_layout_plan(
    topic_ids: Iterable[str],
    *,
    recent_history: Iterable[dict[str, Any]] = (),
    seed_text: str = "",
    style_layout_locked: bool = False,
) -> LayoutPlan:
    """导演两次失败后的确定性安全回退，不创建或改写任何聊天事实。"""
    expected = _expected_topic_ids(topic_ids)
    history = list(recent_history)
    avoided_layouts = set(_history_layout_ids(history)[:3]) if not style_layout_locked else set()
    candidates = [layout_id for layout_id in IMAGE_LAYOUT_KEYS if layout_id not in avoided_layouts]
    if not candidates:
        candidates = list(IMAGE_LAYOUT_KEYS)
    digest = hashlib.sha256((seed_text + "|" + "|".join(expected)).encode("utf-8")).hexdigest()
    layout_id = candidates[int(digest[:8], 16) % len(candidates)]
    avoided_devices = set(_history_comedy_devices(history)[:2])
    devices = [item for item in COMEDY_DEVICES if item not in avoided_devices] or list(COMEDY_DEVICES)
    device = devices[int(digest[8:16], 16) % len(devices)]
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        "hero_rhythm",
        (expected[0],),
        tuple(expected),
        _fallback_beats(expected),
        device,
        "分镜导演响应无效，使用避开近期骨架的确定性大小格结构",
        style_layout_locked=style_layout_locked,
    )


def resolved_layout_instruction(plan: LayoutPlan, custom_style_text: str = "") -> str:
    definition = IMAGE_LAYOUT_DEFINITIONS[plan.layout_id]
    topic_positions = {
        topic_id: f"第{index}段剧情"
        for index, topic_id in enumerate(plan.topic_order, start=1)
    }
    if plan.style_layout_locked:
        priority = (
            "【大主题】中的用户分镜要求拥有最高结构优先级；下述骨架只补充镜头角色和阅读顺序，"
            "冲突部分以用户要求为准。"
        )
    else:
        priority = (
            "本骨架只控制漫画格子、阅读路径和镜头节奏；不得改变【指定风格】中的配色、画材、"
            "人物造型、纹理、光影和统一画风。"
        )
    if plan.structure_mode == "hero_rhythm":
        distribution = f"由{topic_positions[plan.featured_topic_ids[0]]}占据最大场景格并带动后续反应，但面积不超过内容区约 35%。"
    elif plan.structure_mode == "dual_rhythm":
        distribution = "由" + "、".join(topic_positions[item] for item in plan.featured_topic_ids) + "构成两个面积不同的焦点场景并交替推进。"
    else:
        distribution = "使用群像错落节奏，不设唯一主角，但仍必须有大、中、小格三级尺寸差。"
    beat_lines = []
    for beat in plan.panel_beats:
        labels = " → ".join(SHOT_LABELS[shot] for shot in beat.shots)
        beat_lines.append(f"{topic_positions[beat.topic_id]}：{labels}")
    return (
        f"漫画分镜骨架：{definition.label}。{priority}\n"
        f"格子尺寸签名：{definition.size_signature}；必须肉眼可见至少三级尺寸差。\n"
        f"阅读路径：{definition.reading_path}。\n"
        f"页面结构：{definition.instruction}\n"
        f"内容节奏：{STRUCTURE_MODE_LABELS[plan.structure_mode]}（{plan.structure_mode}）。{distribution}\n"
        f"阅读顺序：从第1段剧情依次到第{len(plan.topic_order)}段剧情。总镜头数：{plan.panel_count}；一个话题不等于一个矩形模块，"
        "同一话题的连续镜头应共享人物、动作或气泡衔接。\n"
        "逐段镜头节拍（以下段落编号只用于导演理解，不要画进图片）：\n- "
        + "\n- ".join(beat_lines)
        + f"\n主要喜剧机制：{plan.comedy_device}。人物、道具或效果线可有一次跨格，"
        "禁止整齐两列等高矩形、等大圆角模块、数据面板或列表式排版。"
    )


def layout_plan_json(plan: LayoutPlan) -> str:
    definition = IMAGE_LAYOUT_DEFINITIONS[plan.layout_id]
    return json.dumps(
        {
            "layout_id": plan.layout_id,
            "layout_name": plan.layout_name,
            "panel_size_signature": definition.size_signature,
            "reading_path": definition.reading_path,
            "structure_mode": plan.structure_mode,
            "featured_topic_ids": list(plan.featured_topic_ids),
            "topic_order": list(plan.topic_order),
            "panel_beats": [beat.to_dict() for beat in plan.panel_beats],
            "panel_count": plan.panel_count,
            "comedy_device": plan.comedy_device,
            "layout_reason": plan.layout_reason,
            "style_layout_locked": plan.style_layout_locked,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
