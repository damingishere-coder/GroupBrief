"""V2 生图主题目录、每日可复现随机组合与自定义校验。"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date
from typing import Any


class ImageThemeError(ValueError):
    """生图主题配置不合法。"""


@dataclass(frozen=True)
class ImageThemeDefinition:
    key: str
    label: str
    description: str
    prompt: str


@dataclass(frozen=True)
class ResolvedImageTheme:
    """一次 Prompt 构建实际使用的主题。"""

    requested_key: str
    actual_key: str
    display_name: str
    prompt: str
    custom_text: str = ""
    style_signature: str = ""
    style_seed: str = ""
    catalog_version: str = ""

    def to_meta(self) -> dict[str, str]:
        return {
            "requested_theme": self.requested_key,
            "resolved_theme": self.actual_key,
            "theme_display_name": self.display_name,
            "theme_prompt": self.prompt,
            "theme_text": f"{self.display_name}：{self.prompt}",
            "theme_custom": self.custom_text,
            "style_signature": self.style_signature,
            "style_seed": self.style_seed,
            "style_catalog_version": self.catalog_version,
        }


DEFAULT_IMAGE_THEME = "random_preset"
RANDOM_PRESET_THEME = "random_preset"
AI_FREE_THEME = "ai_free"
CUSTOM_THEME = "custom"
STYLE_CATALOG_VERSION = "daily-style-v2"

# 旧主题继续接受和解析，供历史配置与已保存 Prompt 使用；主界面不再展示。
CONCRETE_THEME_KEYS: tuple[str, ...] = (
    "blue_white",
    "ultraman",
    "pink",
    "bull",
    "retro_newspaper",
    "cyber_neon",
    "guochao",
    "scrapbook",
)

IMAGE_THEME_DEFINITIONS: dict[str, ImageThemeDefinition] = {
    "blue_white": ImageThemeDefinition("blue_white", "默认蓝白漫画", "清爽蓝白配色", "蓝白主色、清爽漫画线稿、留白充足、中文大字信息图排版。"),
    "ultraman": ImageThemeDefinition("ultraman", "原创科幻英雄", "热血科幻英雄感", "热血科幻英雄漫画风，红银蓝能量光效、原创巨大英雄剪影和戏剧化仰角构图；不得复制具体角色。"),
    "pink": ImageThemeDefinition("pink", "粉红色", "轻盈活泼的社交海报", "粉红、白色与少量珊瑚色，甜酷漫画贴纸、柔和渐变和活泼圆角排版。"),
    "bull": ImageThemeDefinition("bull", "牛牛", "喜庆有力量的吉祥漫画", "喜庆红金配色、原创可爱牛形象、金色装饰和稳重构图；牛只能作为装饰。"),
    "retro_newspaper": ImageThemeDefinition("retro_newspaper", "复古报纸", "旧报纸与新闻漫画感", "复古报纸网格、米白纸张、黑红套印、铅字标题和新闻漫画插图质感。"),
    "cyber_neon": ImageThemeDefinition("cyber_neon", "赛博霓虹", "未来城市信息面板", "深蓝黑底、青紫粉霓虹、赛博网格与发光信息面板，保持中文清晰可读。"),
    "guochao": ImageThemeDefinition("guochao", "国潮", "传统纹样与现代漫画", "朱红、黛青与金色，简化传统纹样、印章和现代漫画信息图排版。"),
    "scrapbook": ImageThemeDefinition("scrapbook", "手账拼贴", "彩纸胶带与手写标注", "手账拼贴、彩纸、胶带、便签和手写标注，层次丰富但留白充足。"),
}

IMAGE_THEME_MODE_DEFINITIONS: dict[str, ImageThemeDefinition] = {
    RANDOM_PRESET_THEME: ImageThemeDefinition(RANDOM_PRESET_THEME, "每日随机", "每群每天独立生成一套可复现风格", "按日期与群聊稳定生成兼容的画材、配色、纹理和光影组合；页面几何由漫画分镜单独决定。"),
    AI_FREE_THEME: ImageThemeDefinition(AI_FREE_THEME, "AI 自由发挥（兼容）", "历史配置兼容模式", "根据当天真实聊天选择一个统一视觉主题；不得新增或改变聊天事实。"),
    CUSTOM_THEME: ImageThemeDefinition(CUSTOM_THEME, "指定风格", "输入 1～80 字的群专属风格词", "严格使用用户指定的视觉主题，完全替代每日随机；只能影响视觉表现。"),
}

IMAGE_THEME_KEYS = frozenset((*CONCRETE_THEME_KEYS, *IMAGE_THEME_MODE_DEFINITIONS))

# 每个家族内部的选择经过人工配伍，不会把所有词库做无约束笛卡尔积。
_STYLE_FAMILIES: tuple[dict[str, Any], ...] = (
    {"label": "丝网印刷社论漫画", "media": ("颗粒丝网印刷", "双色孔版印刷"), "palette": ("群青、奶油白与珊瑚红", "墨绿、米白与亮橙"), "texture": ("纸张颗粒和套色轻微错位", "半调网点与撕纸边缘"), "light": ("平面高对比光影", "清晰块面明暗")},
    {"label": "纸艺立体插画", "media": ("分层剪纸", "立体纸雕"), "palette": ("天蓝、暖黄与番茄红", "鼠尾草绿、杏色与靛蓝"), "texture": ("纤维纸纹与圆润切边", "折纸阴影与手工拼贴边缘"), "light": ("柔和棚拍侧光", "浅景深纸艺投影")},
    {"label": "水彩旅行手账", "media": ("透明水彩线稿", "水彩与彩铅混合"), "palette": ("湖蓝、浅赭与豆沙红", "薄荷绿、柠檬黄与灰紫"), "texture": ("水渍晕染和纸胶带", "彩铅笔触与手写箭头"), "light": ("通透自然光", "柔和晨光氛围")},
    {"label": "复古未来主义", "media": ("复古科幻杂志插画", "几何矢量科幻海报"), "palette": ("深靛蓝、铜橙与象牙白", "紫黑、青绿与暖金"), "texture": ("老印刷网点与金属刻度", "扫描线和细密星尘"), "light": ("边缘霓虹与局部辉光", "戏剧化逆光")},
    {"label": "黏土定格剧场", "media": ("手工黏土定格动画", "软陶微缩场景"), "palette": ("奶油黄、天空蓝与草莓红", "陶土橙、青绿与乳白"), "texture": ("可见手捏纹理与圆角道具", "软陶颗粒和纸板布景"), "light": ("温暖摄影棚柔光", "微缩场景侧逆光")},
    {"label": "木刻新闻画", "media": ("现代木刻版画", "粗线条凸版印刷"), "palette": ("黑、米白与朱红", "藏青、牛皮纸色与橘黄"), "texture": ("刀刻线纹和粗粝纸面", "油墨不均与印章点缀"), "light": ("高反差明暗", "版画式硬边阴影")},
    {"label": "玻璃拟态科技刊", "media": ("半透明玻璃拟态", "清洁三维插画"), "palette": ("冰蓝、白色与荧光青", "深灰蓝、薰衣草紫与亮粉"), "texture": ("磨砂玻璃和细线纹理", "透明折射与柔和渐变"), "light": ("冷色体积光", "柔亮边缘光")},
    {"label": "儿童科普绘本", "media": ("蜡笔与彩铅绘本", "不透明水粉童书插画"), "palette": ("明黄、草绿与天空蓝", "珊瑚红、奶油白与海军蓝"), "texture": ("蜡笔颗粒和手绘图标", "水粉笔触与圆润贴纸"), "light": ("明快均匀光线", "温暖午后光")},
    {"label": "建筑蓝图漫画", "media": ("工程蓝图线稿", "等距轴测技术插画"), "palette": ("蓝底白线与安全橙", "石墨灰、亮蓝与荧光黄"), "texture": ("网格纸、尺寸线和编号章", "铅笔辅助线与半透明线稿"), "light": ("理性均匀照明", "细微高光强化结构")},
    {"label": "织物刺绣拼布", "media": ("刺绣与布艺拼贴", "羊毛毡立体插画"), "palette": ("靛蓝、米白与砖红", "橄榄绿、芥末黄与莓果紫"), "texture": ("织物纹理、针脚和毛毡边缘", "绒线轮廓与布贴层次"), "light": ("柔和室内散射光", "温暖侧光突出纤维")},
)


def normalize_custom_theme(value: Any) -> str:
    if not isinstance(value, str):
        raise ImageThemeError("自定义主题必须是文本")
    text = value.strip()
    if not 1 <= len(text) <= 80:
        raise ImageThemeError("自定义主题必须为去除首尾空白后的 1～80 字")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ImageThemeError("自定义主题不得包含控制字符或换行")
    return text


def validate_image_theme_config(theme: Any, custom: Any = "") -> tuple[str, str]:
    if not isinstance(theme, str) or theme.strip() not in IMAGE_THEME_KEYS:
        raise ImageThemeError(f"未知生图主题：{theme!r}")
    key = theme.strip()
    if custom is None:
        custom = ""
    if not isinstance(custom, str):
        raise ImageThemeError("自定义主题必须是文本")
    custom_text = normalize_custom_theme(custom) if custom.strip() or key == CUSTOM_THEME else ""
    return key, custom_text


validate_theme = validate_image_theme_config


def _signature(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _daily_style(seed_text: str, previous_signature: str = "") -> ResolvedImageTheme:
    for attempt in range(20):
        material = f"{STYLE_CATALOG_VERSION}|{seed_text}|{attempt}"
        seed = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
        picker = random.Random(seed)
        family = picker.choice(_STYLE_FAMILIES)
        media = picker.choice(family["media"])
        palette = picker.choice(family["palette"])
        texture = picker.choice(family["texture"])
        light = picker.choice(family["light"])
        prompt = (
            f"统一采用{media}形式；配色为{palette}；加入{texture}；光影为{light}。"
            "保持中文清晰；所有风格元素只作为视觉表现，不得新增或改变聊天事实。"
        )
        signature = _signature(prompt)
        if signature != previous_signature or attempt == 19:
            return ResolvedImageTheme(
                RANDOM_PRESET_THEME,
                "daily_random",
                family["label"],
                prompt,
                style_signature=signature,
                style_seed=f"{seed:016x}",
                catalog_version=STYLE_CATALOG_VERSION,
            )
    raise AssertionError("每日随机风格解析失败")


def resolve_image_theme(
    theme: str = DEFAULT_IMAGE_THEME,
    custom: str = "",
    *,
    rng: Any | None = None,
    group_key: str = "",
    run_date: str = "",
    previous_signature: str = "",
    persisted_meta: dict[str, Any] | None = None,
) -> ResolvedImageTheme:
    key, custom_text = validate_image_theme_config(theme, custom)
    if key == RANDOM_PRESET_THEME:
        if (
            persisted_meta
            and persisted_meta.get("requested_theme") == RANDOM_PRESET_THEME
            and persisted_meta.get("theme_prompt")
            and persisted_meta.get("style_catalog_version") == STYLE_CATALOG_VERSION
        ):
            return ResolvedImageTheme(
                RANDOM_PRESET_THEME,
                str(persisted_meta.get("resolved_theme") or "daily_random"),
                str(persisted_meta.get("theme_display_name") or "每日随机"),
                str(persisted_meta["theme_prompt"]),
                style_signature=str(persisted_meta.get("style_signature") or ""),
                style_seed=str(persisted_meta.get("style_seed") or ""),
                catalog_version=str(persisted_meta.get("style_catalog_version") or STYLE_CATALOG_VERSION),
            )
        # 兼容旧测试/调用方显式注入的 choice 选择器。
        if rng is not None:
            actual_key = rng.choice(CONCRETE_THEME_KEYS)
            definition = IMAGE_THEME_DEFINITIONS[actual_key]
            return ResolvedImageTheme(key, actual_key, definition.label, definition.prompt, style_signature=_signature(definition.prompt))
        effective_date = run_date or date.today().isoformat()
        return _daily_style(f"{group_key or 'preview'}|{effective_date}", previous_signature)
    if key == CUSTOM_THEME:
        prompt = (
            f"自定义大主题「{custom_text}」：严格使用该指定风格并完全替代随机风格；"
            "只能影响配色、装饰、服装、造型、材质和画风，不能新增或改变聊天事实。"
        )
        return ResolvedImageTheme(key, key, custom_text, prompt, custom_text, _signature(prompt))
    if key == AI_FREE_THEME:
        definition = IMAGE_THEME_MODE_DEFINITIONS[key]
        return ResolvedImageTheme(key, key, definition.label, definition.prompt, style_signature=_signature(definition.prompt))
    definition = IMAGE_THEME_DEFINITIONS[key]
    return ResolvedImageTheme(key, key, definition.label, definition.prompt, style_signature=_signature(definition.prompt))


resolve_theme = resolve_image_theme


def public_image_theme_options() -> list[dict[str, str]]:
    """主界面只展示每日随机与指定风格两种模式。"""
    return [
        {"key": definition.key, "label": definition.label, "description": definition.description}
        for definition in (
            IMAGE_THEME_MODE_DEFINITIONS[RANDOM_PRESET_THEME],
            IMAGE_THEME_MODE_DEFINITIONS[CUSTOM_THEME],
        )
    ]
