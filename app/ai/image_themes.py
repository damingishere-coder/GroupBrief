"""V2 生图主题目录、每日可复现随机组合与自定义校验。"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal


class ImageThemeError(ValueError):
    """生图主题配置不合法。"""


ThemeKind = Literal["mode", "preset"]


@dataclass(frozen=True)
class ImageThemeDefinition:
    key: str
    label: str
    description: str
    prompt: str
    kind: ThemeKind = "preset"
    category: str = ""
    swatches: tuple[str, ...] = ()
    variation_count: int = 1


@dataclass(frozen=True)
class StyleFamilyDefinition:
    """一个可公开选择、每天在家族内细微变化的美术风格。"""

    key: str
    label: str
    category: str
    description: str
    swatches: tuple[str, str, str]
    media: tuple[str, str]
    palette: tuple[str, str]
    texture: tuple[str, str]
    light: tuple[str, str]

    @property
    def variation_count(self) -> int:
        return len(self.media) * len(self.palette) * len(self.texture) * len(self.light)


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

    @property
    def has_explicit_style(self) -> bool:
        """只有手动预设、每日随机或自定义主题才注入具体风格约束。"""
        return self.requested_key != AI_FREE_THEME

    @property
    def visible_text(self) -> str:
        """写入 Prompt 的风格文本；AI 自由发挥只保留一条中性说明。"""
        if not self.has_explicit_style:
            return self.prompt
        return f"{self.display_name}：{self.prompt}"

    def to_meta(self) -> dict[str, str]:
        return {
            "requested_theme": self.requested_key,
            "resolved_theme": self.actual_key,
            "theme_display_name": self.display_name,
            "theme_prompt": self.prompt,
            "theme_text": self.visible_text,
            "theme_custom": self.custom_text,
            "style_signature": self.style_signature,
            "style_seed": self.style_seed,
            "style_catalog_version": self.catalog_version,
        }


DEFAULT_IMAGE_THEME = "ai_free"
RANDOM_PRESET_THEME = "random_preset"
AI_FREE_THEME = "ai_free"
CUSTOM_THEME = "custom"
STYLE_CATALOG_VERSION = "daily-style-v3"
STYLE_VARIATIONS_PER_FAMILY = 16
STYLE_SAFETY_SUFFIX = "只控制美术语言和视觉质感，不得新增、删除或改写聊天事实、人物、数字和指定文字。"

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
    RANDOM_PRESET_THEME: ImageThemeDefinition(
        RANDOM_PRESET_THEME, "每日随机", "每群每天从完整风格库生成一套可复现风格",
        "按日期与群聊稳定生成兼容的画材、配色、纹理和光影组合；页面几何由漫画分镜单独决定。",
        kind="mode", category="模式", variation_count=352,
    ),
    AI_FREE_THEME: ImageThemeDefinition(
        AI_FREE_THEME, "AI 自由发挥", "默认不指定画材、配色、纹理或光影",
        "根据当天真实聊天内容自由选择统一视觉风格。",
        kind="mode", category="模式",
    ),
    CUSTOM_THEME: ImageThemeDefinition(
        CUSTOM_THEME, "指定风格", "输入 1～80 字的群专属风格词",
        "严格使用用户指定的视觉主题，完全替代每日随机；只能影响视觉表现。",
        kind="mode", category="模式",
    ),
}


STYLE_FAMILIES: tuple[StyleFamilyDefinition, ...] = (
    StyleFamilyDefinition(
        "silkscreen_editorial", "丝网印刷社论漫画", "印刷与编辑", "粗线、套色与纸张颗粒形成醒目的社论漫画感。",
        ("#21409A", "#F6E8C9", "#F25F5C"),
        ("颗粒丝网印刷", "双色孔版印刷"), ("群青、奶油白与珊瑚红", "墨绿、米白与亮橙"),
        ("纸张颗粒和轻微套色错位", "半调网点与撕纸边缘"), ("平面高对比光影", "清晰块面明暗"),
    ),
    StyleFamilyDefinition(
        "paper_cut_layered", "分层纸艺插画", "立体与手作", "纤维纸、手工切边与柔和投影带来轻巧层次。",
        ("#63B3ED", "#F6C453", "#E34D3B"),
        ("分层剪纸", "立体纸雕"), ("天蓝、暖黄与番茄红", "鼠尾草绿、杏色与靛蓝"),
        ("纤维纸纹与圆润切边", "折纸阴影与手工拼贴边缘"), ("柔和棚拍侧光", "浅景深纸艺投影"),
    ),
    StyleFamilyDefinition(
        "watercolor_journal", "水彩旅行手账", "绘画与纸本", "透明水彩、彩铅和自然纸纹营造轻松手账感。",
        ("#4FA3B7", "#B8D8BA", "#C97B84"),
        ("透明水彩细墨线", "水彩与彩铅混合"), ("湖蓝、浅赭与豆沙红", "薄荷绿、柠檬黄与灰紫"),
        ("水渍晕染与纸胶带质感", "彩铅笔触与棉纸纹理"), ("通透自然光", "柔和晨光氛围"),
    ),
    StyleFamilyDefinition(
        "retro_futurism", "复古未来主义", "科技与结构", "旧科幻印刷语言与克制辉光结合的未来想象。",
        ("#1E2A5E", "#C56E33", "#F2E9D8"),
        ("复古科幻杂志插画", "几何矢量科幻海报"), ("深靛蓝、铜橙与象牙白", "紫黑、青绿与暖金"),
        ("老印刷网点与金属刻度", "扫描线与细密星尘"), ("边缘霓虹与局部辉光", "戏剧化逆光"),
    ),
    StyleFamilyDefinition(
        "clay_stopmotion", "黏土定格剧场", "立体与手作", "手捏质感与微缩摄影让群聊像一场定格短片。",
        ("#F2C94C", "#5DADE2", "#E96B6B"),
        ("手工黏土定格动画", "软陶微缩场景"), ("奶油黄、天空蓝与草莓红", "陶土橙、青绿与乳白"),
        ("可见手捏纹理与圆角道具", "软陶颗粒与纸板布景"), ("温暖摄影棚柔光", "微缩场景侧逆光"),
    ),
    StyleFamilyDefinition(
        "woodcut_editorial", "木刻新闻画", "印刷与编辑", "粗线刀刻、套印墨色与强反差形成有力叙事。",
        ("#171717", "#F3E6C8", "#B52A2A"),
        ("现代木刻版画", "粗线凸版印刷"), ("黑、米白与朱红", "藏青、牛皮纸色与橘黄"),
        ("刀刻线纹与粗粝纸面", "油墨不均与印章色点"), ("高反差明暗", "版画式硬边阴影"),
    ),
    StyleFamilyDefinition(
        "glassmorphism_tech", "玻璃拟态科技刊", "科技与结构", "透明折射、磨砂表面与冷色辉光呈现清洁科技感。",
        ("#25304A", "#67E8F9", "#A78BFA"),
        ("半透明玻璃拟态插画", "清洁三维科技插画"), ("冰蓝、白色与荧光青", "深灰蓝、薰衣草紫与亮粉"),
        ("磨砂玻璃与细线纹理", "透明折射与柔和渐变"), ("冷色体积光", "柔亮边缘光"),
    ),
    StyleFamilyDefinition(
        "children_science_picturebook", "儿童科普绘本", "绘画与纸本", "蜡笔、水粉和圆润造型带来明快、亲切的科普绘本感。",
        ("#F5C542", "#67B76F", "#5AA7E8"),
        ("蜡笔与彩铅绘本", "不透明水粉童书插画"), ("明黄、草绿与天空蓝", "珊瑚红、奶油白与海军蓝"),
        ("蜡笔颗粒与手绘符号", "水粉笔触与圆润纸贴质感"), ("明快均匀光线", "温暖午后光"),
    ),
    StyleFamilyDefinition(
        "architectural_blueprint", "建筑蓝图漫画", "科技与结构", "工程线稿、轴测绘制与克制高光形成理性视觉语言。",
        ("#165DFF", "#F8FAFC", "#FF8A34"),
        ("工程蓝图线稿", "等距轴测技术插画"), ("蓝底白线与安全橙", "石墨灰、亮蓝与荧光黄"),
        ("工程网格与细线刻痕", "铅笔辅助线与半透明线稿"), ("理性均匀照明", "细微高光强化形体"),
    ),
    StyleFamilyDefinition(
        "textile_embroidery", "织物刺绣拼布", "立体与手作", "针脚、毛毡和布贴层次形成温暖的手作触感。",
        ("#344E7A", "#F4ECD8", "#A64B3C"),
        ("刺绣与布艺拼贴", "羊毛毡立体插画"), ("靛蓝、米白与砖红", "橄榄绿、芥末黄与莓果紫"),
        ("织物纹理、针脚与毛毡边缘", "绒线轮廓与布贴层次"), ("柔和室内散射光", "温暖侧光突出纤维"),
    ),
    StyleFamilyDefinition(
        "ink_wash_editorial", "水墨留白漫画", "传统与复古", "干湿笔、宣纸留白与克制设色呈现代水墨叙事。",
        ("#1B1D1F", "#264653", "#C43D2F"),
        ("现代水墨细线漫画", "水墨设色干湿笔"), ("墨黑、黛青、朱砂与宣纸白", "松烟黑、石青、赭石与暖白"),
        ("宣纸纤维、飞白与墨晕", "枯笔皴擦、水痕与印章色点"), ("雾化散射与局部硬墨", "柔亮留白与深墨对比"),
    ),
    StyleFamilyDefinition(
        "art_deco_night", "装饰艺术夜刊", "传统与复古", "对称几何、金属质感与深色调形成精致夜刊气氛。",
        ("#0D3B2E", "#D4AF37", "#F5E6C8"),
        ("几何装饰插画", "金属箔复古印刷"), ("深祖母绿、黑金与奶油色", "午夜蓝、酒红与香槟金"),
        ("压纹纸与细金线", "天鹅绒颗粒与金箔"), ("琥珀聚光", "高对比金属边缘光"),
    ),
    StyleFamilyDefinition(
        "isometric_miniature", "等距微缩模型", "立体与手作", "俯视微缩物件与短投影构成清爽的立体小世界。",
        ("#8EC5FC", "#F9C74F", "#90BE6D"),
        ("等距微缩模型", "低多边形立体插画"), ("天空蓝、暖黄与草绿", "鼠尾草绿、陶土橙与乳白"),
        ("磨砂模型树脂", "微缩木材与颗粒地形"), ("柔和俯视棚拍", "清晰日光与短投影"),
    ),
    StyleFamilyDefinition(
        "pixel_arcade", "像素街机小志", "动漫与数字", "清晰像素簇、街机色彩与屏幕辉光形成活跃数字质感。",
        ("#2B174A", "#00D4FF", "#FF4D8D"),
        ("16-bit 像素画", "32-bit 像素插画"), ("紫黑、荧光青与亮粉", "深蓝、青柠绿与亮橙"),
        ("抖动网点与像素簇", "扫描线与精细精灵边缘"), ("屏幕霓虹辉光", "街机式高对比光"),
    ),
    StyleFamilyDefinition(
        "cel_animation", "赛璐璐动画", "动漫与数字", "平涂色块、手绘墨线和戏剧轮廓光带来动画张力。",
        ("#243B6B", "#F2C14E", "#E85D75"),
        ("手绘赛璐璐动画", "复古电视动画"), ("深蓝、明黄与珊瑚红", "青绿、奶油白与绯红"),
        ("平涂色块、墨线与胶片尘点", "赛璐璐颜料边缘与轻微套色"), ("戏剧性轮廓光", "暖色夕照补光"),
    ),
    StyleFamilyDefinition(
        "chibi_sticker", "Q版贴纸剧场", "动漫与数字", "圆润人物、白边贴纸和糖果色让群聊轻松可爱。",
        ("#F8BBD0", "#B39DDB", "#81D4FA"),
        ("光泽 Q 版贴纸", "圆润萌系插画"), ("粉红、薰衣草紫与天空蓝", "柠檬黄、薄荷绿与珊瑚红"),
        ("白色贴纸边与高光", "柔软塑料颗粒与纸标签质感"), ("柔和正面光", "糖果色环境光"),
    ),
    StyleFamilyDefinition(
        "pencil_storyboard", "铅笔分镜手稿", "绘画与纸本", "石墨线、纸纹和速写痕迹保留鲜活的创作现场感。",
        ("#4A4A4A", "#D9CBB6", "#B76E79"),
        ("石墨铅笔手稿", "彩铅速写"), ("石墨灰、米纸色与红铅色", "炭黑、象牙白与靛蓝"),
        ("纸纹、橡皮痕与辅助线", "交叉排线与纸胶带质感"), ("桌面散射光", "窗边柔和侧光"),
    ),
    StyleFamilyDefinition(
        "natural_history_engraving", "复古博物图鉴", "传统与复古", "精密排线、旧纸斑点与博物馆光线呈现古典图鉴气质。",
        ("#5B4636", "#C9B27C", "#6B7D4E"),
        ("铜版蚀刻插画", "钢笔线描图鉴"), ("棕褐、橄榄绿与砖红", "墨黑、旧纸色与深蓝"),
        ("铜版排线与旧纸斑点", "细密交叉线与纤维纸"), ("博物馆柔光", "轻微暗角侧光"),
    ),
    StyleFamilyDefinition(
        "minimal_vector", "极简几何插画", "印刷与编辑", "大色块、清晰轮廓与少量错位投影保持利落现代。",
        ("#111827", "#F9FAFB", "#FF6B35"),
        ("扁平矢量插画", "几何拼贴插画"), ("黑、白与亮橙", "深蓝、奶油白与草绿"),
        ("纯色色块与清晰轮廓", "切割几何表面"), ("均匀平面光", "轻微错位投影"),
    ),
    StyleFamilyDefinition(
        "gouache_editorial", "不透明水粉社论", "绘画与纸本", "厚实哑光笔触与纸张颗粒形成温暖的社论插画感。",
        ("#D95D39", "#E9C46A", "#2A9D8F"),
        ("不透明水粉插画", "水粉与彩铅混合"), ("赭黄、珊瑚红与青绿", "灰紫、芥末黄与海军蓝"),
        ("厚实笔触与纸张颗粒", "叠色水粉与干刷边缘"), ("柔和哑光", "温暖散射光"),
    ),
    StyleFamilyDefinition(
        "stained_glass", "彩色玻璃拼画", "立体与手作", "铅条接缝、宝石色玻璃与透射光形成璀璨手作质感。",
        ("#2E1A47", "#1F7A8C", "#C99700"),
        ("铅条彩色玻璃", "手工玻璃马赛克"), ("紫晶、青绿与金黄", "宝石红、钴蓝与琥珀色"),
        ("玻璃气泡与铅条接缝", "不规则马赛克边缘"), ("透射辉光", "高对比彩色折射光"),
    ),
    StyleFamilyDefinition(
        "mineral_pigment", "矿物岩彩插画", "传统与复古", "矿物颗粒、石面底色与细碎金箔呈现沉稳华丽的岩彩效果。",
        ("#B33A3A", "#235789", "#C6A15B"),
        ("矿物岩彩插画", "石色设色绘画"), ("朱砂、石青、石绿与金色", "赭石、靛蓝、玉绿与象牙白"),
        ("矿物颗粒与灰泥底", "石面纹理与细碎金箔"), ("掠射侧光", "柔和宝石反射光"),
    ),
)

STYLE_FAMILY_BY_KEY = {family.key: family for family in STYLE_FAMILIES}
STYLE_FAMILY_KEYS = tuple(family.key for family in STYLE_FAMILIES)
IMAGE_THEME_KEYS = frozenset((*CONCRETE_THEME_KEYS, *IMAGE_THEME_MODE_DEFINITIONS, *STYLE_FAMILY_KEYS))

_HEX_COLOR_RE = re.compile(r"^#[0-9A-F]{6}$")
_PERSISTED_STYLE_FORBIDDEN = (
    "版式使用", "卡片", "数据面板", "分栏", "跨格", "路线式阅读", "信息节点", "中心主视觉", "REFERENCE_0",
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
    custom_text = normalize_custom_theme(custom) if key == CUSTOM_THEME else ""
    return key, custom_text


validate_theme = validate_image_theme_config


def _signature(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def _style_prompt(family: StyleFamilyDefinition, picker: random.Random) -> str:
    media = picker.choice(family.media)
    palette = picker.choice(family.palette)
    texture = picker.choice(family.texture)
    light = picker.choice(family.light)
    return f"统一采用{media}；配色为{palette}；质感为{texture}；光影为{light}。{STYLE_SAFETY_SUFFIX}"


def _daily_style(requested_key: str, group_key: str, run_date: str, previous_signature: str = "") -> ResolvedImageTheme:
    for attempt in range(20):
        material = f"{STYLE_CATALOG_VERSION}|{requested_key}|{group_key}|{run_date}|{attempt}"
        seed = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
        picker = random.Random(seed)
        family = picker.choice(STYLE_FAMILIES) if requested_key == RANDOM_PRESET_THEME else STYLE_FAMILY_BY_KEY[requested_key]
        prompt = _style_prompt(family, picker)
        signature = _signature(prompt)
        if signature != previous_signature or attempt == 19:
            return ResolvedImageTheme(
                requested_key, family.key, family.label, prompt,
                style_signature=signature, style_seed=f"{seed:016x}", catalog_version=STYLE_CATALOG_VERSION,
            )
    raise AssertionError("每日风格解析失败")


def _restored_style(key: str, persisted_meta: dict[str, Any] | None) -> ResolvedImageTheme | None:
    if not persisted_meta or persisted_meta.get("requested_theme") != key:
        return None
    version = str(persisted_meta.get("style_catalog_version") or "")
    prompt = str(persisted_meta.get("theme_prompt") or "").strip()
    if version not in {"daily-style-v2", STYLE_CATALOG_VERSION} or not prompt:
        return None
    if any(term in prompt for term in _PERSISTED_STYLE_FORBIDDEN):
        return None
    return ResolvedImageTheme(
        key,
        str(persisted_meta.get("resolved_theme") or ("daily_random" if key == RANDOM_PRESET_THEME else key)),
        str(persisted_meta.get("theme_display_name") or "每日随机"),
        prompt,
        style_signature=str(persisted_meta.get("style_signature") or _signature(prompt)),
        style_seed=str(persisted_meta.get("style_seed") or ""),
        catalog_version=version,
    )


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
    if key == RANDOM_PRESET_THEME or key in STYLE_FAMILY_BY_KEY:
        restored = _restored_style(key, persisted_meta)
        if restored is not None:
            return restored
        # 兼容旧测试/调用方显式注入的 choice 选择器。
        if key == RANDOM_PRESET_THEME and rng is not None:
            actual_key = rng.choice(CONCRETE_THEME_KEYS)
            definition = IMAGE_THEME_DEFINITIONS[actual_key]
            return ResolvedImageTheme(key, actual_key, definition.label, definition.prompt, style_signature=_signature(definition.prompt))
        effective_date = run_date or date.today().isoformat()
        return _daily_style(key, group_key or "preview", effective_date, previous_signature)
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


def _public_option(definition: ImageThemeDefinition) -> dict[str, object]:
    return {
        "key": definition.key,
        "label": definition.label,
        "description": definition.description,
        "kind": definition.kind,
        "category": definition.category,
        "swatches": list(definition.swatches),
        "variation_count": definition.variation_count,
        "preview_url": (
            f"/assets/image-theme-previews/{definition.key}.webp"
            if definition.kind == "preset"
            else ""
        ),
    }


def public_image_theme_options() -> list[dict[str, object]]:
    """返回三种选择模式和稳定排序的 22 个公开风格家族。"""
    modes = (
        IMAGE_THEME_MODE_DEFINITIONS[AI_FREE_THEME],
        IMAGE_THEME_MODE_DEFINITIONS[RANDOM_PRESET_THEME],
        IMAGE_THEME_MODE_DEFINITIONS[CUSTOM_THEME],
    )
    presets = (
        ImageThemeDefinition(
            family.key, family.label, family.description, "", kind="preset", category=family.category,
            swatches=family.swatches, variation_count=family.variation_count,
        )
        for family in STYLE_FAMILIES
    )
    return [_public_option(definition) for definition in (*modes, *presets)]


def validate_style_catalog() -> None:
    """启动测试可调用的目录自检；生产解析不在请求热路径重复运行。"""
    if len(STYLE_FAMILIES) != 22 or len(STYLE_FAMILY_BY_KEY) != len(STYLE_FAMILIES):
        raise ImageThemeError("公开风格家族必须恰好为 22 个且键唯一")
    for family in STYLE_FAMILIES:
        if family.variation_count != STYLE_VARIATIONS_PER_FAMILY:
            raise ImageThemeError(f"风格 {family.key} 的变化数量不是 16")
        if len(family.swatches) != 3 or any(not _HEX_COLOR_RE.fullmatch(color) for color in family.swatches):
            raise ImageThemeError(f"风格 {family.key} 的色板不合法")
