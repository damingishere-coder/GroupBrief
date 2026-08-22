"""整张日报海报的 12 种结构目录、导演结果校验与安全回退。

版式只控制宏观区域、阅读路径和事件角色。配色、画材、服装、造型、
纹理、光影与统一画风始终由 image_theme / image_theme_custom 控制。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


LAYOUT_CATALOG_VERSION = "poster-layout-v1"

COMEDY_DEVICES: tuple[str, ...] = (
    "字面化",
    "反差",
    "回环",
    "误会与反转",
    "一本正经地荒诞",
)


class LayoutPlanError(ValueError):
    """版式导演返回了无法安全使用的结构。"""


@dataclass(frozen=True)
class ImageLayoutDefinition:
    key: str
    label: str
    best_for: str
    instruction: str


@dataclass(frozen=True)
class LayoutPlan:
    layout_id: str
    layout_name: str
    hero_topic_id: str
    support_topic_ids: tuple[str, ...]
    comedy_device: str
    layout_reason: str
    style_layout_locked: bool = False
    reused: bool = False

    @property
    def signature(self) -> str:
        material = "|".join(
            (
                LAYOUT_CATALOG_VERSION,
                self.layout_id,
                self.hero_topic_id,
                *self.support_topic_ids,
                self.comedy_device,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def to_meta(self) -> dict[str, Any]:
        return {
            "layout_catalog_version": LAYOUT_CATALOG_VERSION,
            "layout_id": self.layout_id,
            "layout_name": self.layout_name,
            "layout_signature": self.signature,
            "hero_topic_id": self.hero_topic_id,
            "support_topic_ids": list(self.support_topic_ids),
            "comedy_device": self.comedy_device,
            "layout_reason": self.layout_reason,
            "style_layout_locked": self.style_layout_locked,
            "layout_reused": self.reused,
        }


IMAGE_LAYOUT_DEFINITIONS: dict[str, ImageLayoutDefinition] = {
    "hero_cover": ImageLayoutDefinition(
        "hero_cover",
        "头条封面",
        "有一个绝对主事件，其余话题适合作为短讯",
        "整张海报采用不对称头条封面结构：主梗占约一半面积并形成唯一视觉中心；"
        "其余事件作为 1～4 个较小角标或短讯区围绕主视觉；日期和给定数据集中放在清晰的信息栏。",
    ),
    "comic_strip": ImageLayoutDefinition(
        "comic_strip",
        "四格连续剧",
        "存在起因、发展、反转或连续对话",
        "整张海报采用纵向连续漫画结构：按起因、升级、反转、收尾形成 2～5 个连续画格；"
        "主梗获得最大的反转画格，辅助事件进入较小过场格；阅读顺序必须一眼可辨。",
    ),
    "group_court": ImageLayoutDefinition(
        "group_court",
        "群聊法庭",
        "争论、规则、投诉、观点冲突或翻车事件",
        "整张海报采用法庭叙事结构：主梗是中央主案件；真实对立观点分置两侧；"
        "辅助事件作为证物卡或庭外快讯；群友反应位于陪审区；日期和数据放在结案栏。",
    ),
    "variety_arena": ImageLayoutDefinition(
        "variety_arena",
        "综艺擂台",
        "比较、投票、竞猜、方案竞争或站队",
        "整张海报采用中央擂台结构：主梗位于舞台中心，比较对象或真实观点分列左右；"
        "辅助事件进入计分板、抢答席或观众反应区；底部以节目收官栏承载日期和给定数据。",
    ),
    "detective_wall": ImageLayoutDefinition(
        "detective_wall",
        "侦探证据墙",
        "谜题、技术排错、身份竞猜、原因追查",
        "整张海报采用中心谜题加环绕线索的证据墙结构：主梗位于中心；"
        "辅助事件和真实原话成为四周线索卡，通过清晰连线形成阅读路径；结论区不得超出聊天证据。",
    ),
    "adventure_map": ImageLayoutDefinition(
        "adventure_map",
        "冒险地图",
        "一天话题跨度大，适合用路线串联",
        "整张海报采用自上而下的路线地图结构：主梗是最大的核心关卡；"
        "辅助事件沿单一路径成为 1～4 个节点；起点、转折和终点层级明确；数据位于路线终点。",
    ),
    "awards_night": ImageLayoutDefinition(
        "awards_night",
        "群聊颁奖礼",
        "成就、金句、里程碑或多人表现",
        "整张海报采用中央颁奖舞台结构：主梗获得最高奖项和最大区域；"
        "辅助事件各占一个奖项卡；真实人物和原话进入领奖与观众区；日期和数据作为谢幕信息。",
    ),
    "launch_event": ImageLayoutDefinition(
        "launch_event",
        "新品发布会",
        "产品发布、功能更新、技术教程或方案说明",
        "整张海报采用主舞台发布结构：主梗在中央大屏或展示台；"
        "辅助事件作为功能卡、演示窗口或问答区；主次区域明显，日期和给定数据位于发布信息栏。",
    ),
    "newsroom_live": ImageLayoutDefinition(
        "newsroom_live",
        "新闻直播间",
        "多个并行热点，信息节奏快",
        "整张海报采用主播区加现场连线窗口的直播结构：主梗是主新闻画面；"
        "辅助事件进入 1～4 个连线窗口或快讯区；日期和给定数据集中在底部播报栏。",
    ),
    "daily_menu": ImageLayoutDefinition(
        "daily_menu",
        "今日菜单",
        "话题混杂、轻松，没有绝对统一叙事类型",
        "整张海报采用菜单式主次结构：主梗作为占比最大的今日主菜；"
        "辅助事件作为 1～4 个小菜或加餐区；真实原话作为短评；日期和给定数据放在结算栏。",
    ),
    "topic_orbit": ImageLayoutDefinition(
        "topic_orbit",
        "话题星系",
        "一个中心话题带出多个关联分支",
        "整张海报采用中心加轨道环绕结构：主梗位于中央；"
        "辅助事件沿清晰的不同层级轨道排列，连线只表达话题关系而不是新增事实；数据位于外圈信息带。",
    ),
    "ensemble_theater": ImageLayoutDefinition(
        "ensemble_theater",
        "群像剧场",
        "人物互动、原话和群体反应比事件本身更精彩",
        "整张海报采用中央主舞台加两侧副舞台的群像结构：主梗占中央；"
        "辅助事件进入侧台、观众席或幕后短场；真实对话承担节奏；日期和给定数据作为谢幕栏。",
    ),
}

IMAGE_LAYOUT_KEYS = tuple(IMAGE_LAYOUT_DEFINITIONS)


LAYOUT_DIRECTOR_SYSTEM = """你是 GroupBrief 的整张海报版式导演。
只能使用给定的已入选主题，必须返回一个 JSON 对象，不得输出 Markdown 或解释。

事实真实性是准入门槛；在真实方案中，好玩程度是第一优化目标。选择一个主梗，
其余主题全部作为辅助事件且每个恰好使用一次。整张海报只能选择一种整体版式。

指定风格是全图最高视觉约束，版式不能修改或削弱其配色、画材、人物服装、人物造型、
装饰、纹理、光影和画风。版式只控制宏观区域、阅读顺序和事件主次。

优先选择最符合事件关系的版式；不得选择前一次使用的版式，并尽量避开最近三次。
法庭、擂台、菜单、星系等都是视觉叙事隐喻，不能当作真实聊天事件。"""


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_EXPLICIT_LAYOUT_HINTS = (
    "版式",
    "布局",
    "构图",
    "四格",
    "连环画",
    "三栏",
    "分栏",
    "头版",
    "封面",
    "法庭",
    "擂台",
    "证据墙",
    "路线图",
    "冒险地图",
    "颁奖",
    "发布会",
    "直播间",
    "演播室",
    "菜单",
    "星系",
    "轨道",
    "剧场",
    "舞台",
)

_EXPLICIT_LAYOUT_MAP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("四格", "连环画"), "comic_strip"),
    (("法庭",), "group_court"),
    (("擂台", "PK台", "pk台"), "variety_arena"),
    (("证据墙", "侦探墙"), "detective_wall"),
    (("冒险地图", "路线图"), "adventure_map"),
    (("颁奖",), "awards_night"),
    (("发布会",), "launch_event"),
    (("直播间", "演播室"), "newsroom_live"),
    (("菜单",), "daily_menu"),
    (("星系", "轨道"), "topic_orbit"),
    (("剧场", "群像舞台"), "ensemble_theater"),
    (("三栏", "头版", "封面"), "hero_cover"),
)


def detect_explicit_style_layout(custom_text: str) -> bool:
    """保守识别用户自定义风格中明确写出的版式要求。"""
    text = (custom_text or "").strip()
    return bool(text and any(hint in text for hint in _EXPLICIT_LAYOUT_HINTS))


def preferred_layout_from_style(custom_text: str) -> str:
    """将可明确映射的用户版式词转换为目录 ID；未知描述交给风格优先规则。"""
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
        {"layout_id": item.key, "name": item.label, "best_for": item.best_for}
        for item in IMAGE_LAYOUT_DEFINITIONS.values()
    ]
    return (
        "请为这组已校验主题选择整张海报的唯一版式和主梗。\n\n"
        "返回结构：\n"
        '{"layout_id":"12种ID之一","hero_topic_id":"一个入选ID",'
        '"support_topic_ids":["其余全部入选ID"],'
        '"comedy_device":"字面化/反差/回环/误会与反转/一本正经地荒诞",'
        '"layout_reason":"简短理由"}\n\n'
        f"指定风格（只能服从，不得改写）：{theme_text}\n"
        f"指定风格是否含明确版式要求：{str(style_layout_locked).lower()}\n"
        f"最近版式历史：{json.dumps(history, ensure_ascii=False, separators=(',', ':'))}\n"
        f"可选版式：{json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))}\n"
        f"已入选主题：{selected_topics_payload}"
    )


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
        raise LayoutPlanError(f"版式导演响应不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LayoutPlanError("版式导演响应必须是 JSON 对象")

    expected = [str(topic_id) for topic_id in topic_ids if str(topic_id)]
    if not (2 <= len(expected) <= 5) or len(set(expected)) != len(expected):
        raise LayoutPlanError("入选主题必须是 2～5 个不重复 ID")

    layout_id = str(payload.get("layout_id") or "").strip()
    if layout_id not in IMAGE_LAYOUT_DEFINITIONS:
        raise LayoutPlanError(f"未知整体版式：{layout_id!r}")
    if previous_layout_id and layout_id == previous_layout_id and not style_layout_locked:
        raise LayoutPlanError("整体版式不得与前一次连续重复")

    hero = str(payload.get("hero_topic_id") or "").strip()
    if hero not in expected:
        raise LayoutPlanError("主梗必须来自已入选主题")
    raw_support = payload.get("support_topic_ids")
    if not isinstance(raw_support, list):
        raise LayoutPlanError("support_topic_ids 必须是数组")
    support = tuple(item.strip() for item in raw_support if isinstance(item, str) and item.strip())
    if len(support) != len(set(support)) or hero in support:
        raise LayoutPlanError("主梗和辅助主题不得重复")
    if set(support) != (set(expected) - {hero}):
        raise LayoutPlanError("主梗和辅助主题必须恰好覆盖全部入选主题")

    comedy_device = str(payload.get("comedy_device") or "").strip()
    if comedy_device not in COMEDY_DEVICES:
        raise LayoutPlanError(f"未知喜剧机制：{comedy_device!r}")
    reason = str(payload.get("layout_reason") or "").strip()[:240] or "按事件关系选择整体版式"
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        hero,
        support,
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
    layout_id = str(persisted_meta.get("layout_id") or "")
    if layout_id not in IMAGE_LAYOUT_DEFINITIONS:
        return None
    expected = [str(topic_id) for topic_id in topic_ids if str(topic_id)]
    if not expected:
        return None
    hero = str(persisted_meta.get("hero_topic_id") or "")
    if hero not in expected:
        hero = expected[0]
    support = tuple(topic_id for topic_id in expected if topic_id != hero)
    comedy_device = str(persisted_meta.get("comedy_device") or "")
    if comedy_device not in COMEDY_DEVICES:
        comedy_device = COMEDY_DEVICES[0]
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        hero,
        support,
        comedy_device,
        str(persisted_meta.get("layout_reason") or "同群同日复用已选版式")[:240],
        style_layout_locked=style_layout_locked,
        reused=True,
    )


def fixed_layout_plan(
    layout_id: str,
    topic_ids: Iterable[str],
    *,
    recent_history: Iterable[dict[str, Any]] = (),
) -> LayoutPlan:
    """用户自定义风格明确指定版式时，直接服从该整体结构。"""
    expected = [str(topic_id) for topic_id in topic_ids if str(topic_id)]
    if layout_id not in IMAGE_LAYOUT_DEFINITIONS or not expected:
        raise LayoutPlanError("无法应用指定风格中的整体版式")
    avoided_devices = set(_history_comedy_devices(recent_history)[:2])
    device = next((item for item in COMEDY_DEVICES if item not in avoided_devices), COMEDY_DEVICES[0])
    definition = IMAGE_LAYOUT_DEFINITIONS[layout_id]
    return LayoutPlan(
        layout_id,
        definition.label,
        expected[0],
        tuple(expected[1:]),
        device,
        "用户指定风格中已包含明确版式，自动导演只分配内容角色",
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
    expected = [str(topic_id) for topic_id in topic_ids if str(topic_id)]
    if not expected:
        raise LayoutPlanError("没有可分配的入选主题")
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
        expected[0],
        tuple(expected[1:]),
        device,
        "版式导演响应无效，使用不重复的确定性安全结构",
        style_layout_locked=style_layout_locked,
    )


def resolved_layout_instruction(plan: LayoutPlan, custom_style_text: str = "") -> str:
    definition = IMAGE_LAYOUT_DEFINITIONS[plan.layout_id]
    if plan.style_layout_locked:
        priority = (
            "本次生成时【大主题】中的用户要求含明确版式，该要求拥有最高结构优先级；"
            "下述目录结构只能补充分配主梗、辅助事件和数据位置，任何冲突部分必须忽略。"
        )
    else:
        priority = (
            "本版式控制整张海报的宏观区域、阅读路径和事件主次；"
            "不得改变或削弱【指定风格】中的配色、画材、服装、造型、装饰、纹理、光影和画风。"
        )
    return (
        f"整体版式：{definition.label}（{definition.key}）。{priority}\n"
        f"结构：{definition.instruction}\n"
        f"内容角色：主梗={plan.hero_topic_id}；辅助事件={','.join(plan.support_topic_ids)}。\n"
        f"主要喜剧机制：{plan.comedy_device}。法庭、擂台、菜单、星系等只允许作为视觉隐喻，"
        "不得表述为真实聊天事件。"
    )


def layout_plan_json(plan: LayoutPlan) -> str:
    return json.dumps(
        {
            "layout_id": plan.layout_id,
            "layout_name": plan.layout_name,
            "hero_topic_id": plan.hero_topic_id,
            "support_topic_ids": list(plan.support_topic_ids),
            "comedy_device": plan.comedy_device,
            "layout_reason": plan.layout_reason,
            "style_layout_locked": plan.style_layout_locked,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
