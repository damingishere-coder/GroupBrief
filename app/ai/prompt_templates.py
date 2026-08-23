"""生图 Prompt 模板服务。

模板以 UTF-8 文本文件存储在 templates/image_prompt/ 下（默认 default.md）。
与排行榜模板（app/ranking/template_service.py）结构一致，变量不同：
group_name / report_date / period_start / period_end / message_count / speaker_count /
image_theme / layout_name / layout_instruction。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config.settings import PROJECT_ROOT

# 默认生图 Prompt 模板（与 templates/image_prompt/default.md 同步；恢复默认时写回此内容）
DEFAULT_IMAGE_PROMPT_TEMPLATE = """【任务】
生成一张竖版微信群日报漫画信息图。

【创作优先级】
事实真实性是准入门槛；通过真实性校验后，好玩程度、群内识别度和视觉笑点是第一优化目标。
优先让群友看见“谁参与了什么”；根据当天内容选择单核心、双核心或并列话题，不强造固定主次。

【群名称】
{{group_name}}

【固定画面日期】
统计日期：{{report_date}}

【统计时间】
{{period_start}} ~ {{period_end}}

【数据】
{{message_count}} 条消息
{{speaker_count}} 人发言

【大主题】
{{image_theme}}

【整体版式】
{{layout_name}}
{{layout_instruction}}

【主标题】
（优先使用真实原话、群内梗、反差或回环；建议不超过 18 个汉字，不得挤占信息卡空间）

【副标题】
（一句话概括当天讨论；建议不超过 26 个汉字）

【事件内容分配】
按给定【内容结构】分配全部话题，不得自行改成固定“一个主梗加若干副梗”。
每个话题必须形成独立可读的信息卡，包含：短标题 / 参与群友 / 一句真实事实 / 一条真实原话或关键细节 / 视觉笑点。
程序给定的 2~5 个入选主题必须恰好各使用一次，不得遗漏、重复、增删或改选。

【画面文字白名单】
必须清晰绘制：主标题、统计日期、给定数据，以及每张话题卡的短标题、参与群友、事实信息和给定原话或关键细节。
群友姓名必须进入对应话题卡，不得只画匿名人物；空间不足时先减少装饰和副标题，优先保留姓名与事实。

【底部总结】
可用一句短文案回收当天讨论；不使用“信息量拉满”“一天顶一周”“比过山车还刺激”等通用套话。

【硬性要求】
1. 只使用聊天内容中真实存在的事件、人物、对话，禁止编造。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 原话引用必须来自真实聊天，可适当缩写，但不能改写事实。
4. 可以使用字面化、反差、回环、误会与反转、一本正经地荒诞，但不能改变事实。
5. 海报人物依据「聊天事件中提到的人物」，而不是发言排行榜 Top10；群友署名只能使用程序给定的真实参与者。
6. 数据（消息数、发言人数）必须使用给定数字，禁止自行计算。
7. 【大主题】是全图最高视觉约束，控制配色、画材、服装、造型、装饰、纹理、光影和画风；【整体版式】不得替换或削弱它。
8. 【整体版式】只控制整张图的宏观区域、阅读路径和动态内容层级；每张图只能使用给定的一种版式。
9. 法庭、擂台、菜单、星系等只能作为视觉隐喻，不得表述为真实聊天事件。
10. 最终 Prompt 必须严格包含给定的 2~5 个入选主题且各使用一次；不足两个真实证据主题时应由上游失败，不得编造。
11. 每个入选话题都必须显示至少一个真实群友姓名和一句真实事实信息，不得用泛化头像替代姓名。
12. 必须把“统计日期：{{report_date}}”逐字作为清晰可见的画面文字，放在海报顶部或底部。
"""

# 生图 Prompt 模板支持的变量
IMAGE_PROMPT_VARS = frozenset(
    {
        "group_name",
        "report_date",
        "period_start",
        "period_end",
        "message_count",
        "speaker_count",
        "image_theme",
        "layout_name",
        "layout_instruction",
    }
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_RENDER_DEFAULTS = {
    "layout_name": "（生成时自动选择整张海报版式）",
    "layout_instruction": "（生成时根据入选主题和最近版式历史写入整图结构）",
}


class ImagePromptTemplateError(ValueError):
    """生图 Prompt 模板错误。"""


class ImagePromptTemplateService:
    def __init__(self, templates_dir: Path | None = None):
        self.dir = templates_dir or (PROJECT_ROOT / "templates" / "image_prompt")
        self._ensure_default()

    def _ensure_default(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "default.md"
        if not path.exists():
            path.write_text(DEFAULT_IMAGE_PROMPT_TEMPLATE, encoding="utf-8")

    def _path(self, name: str) -> Path:
        if not name or not _SAFE_NAME_RE.match(name):
            raise ImagePromptTemplateError(f"非法模板名：{name!r}")
        path = self.dir / f"{name}.md"
        if not path.exists():
            raise ImagePromptTemplateError(f"模板不存在：{name}")
        return path

    def list_templates(self) -> list[str]:
        self._ensure_default()
        return sorted(p.stem for p in self.dir.glob("*.md"))

    def read(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def save(self, name: str, content: str) -> None:
        if not name or not _SAFE_NAME_RE.match(name):
            raise ImagePromptTemplateError(f"非法模板名：{name!r}")
        validate_image_prompt_template(content)
        (self.dir / f"{name}.md").write_text(content, encoding="utf-8")

    def delete(self, name: str) -> None:
        if name == "default":
            raise ImagePromptTemplateError("默认模板不可删除")
        self._path(name).unlink()

    def reset(self, name: str = "default") -> str:
        if name != "default":
            raise ImagePromptTemplateError("目前仅支持恢复默认模板")
        (self.dir / "default.md").write_text(DEFAULT_IMAGE_PROMPT_TEMPLATE, encoding="utf-8")
        return DEFAULT_IMAGE_PROMPT_TEMPLATE


def validate_image_prompt_template(text: str) -> None:
    """校验模板：所有 {{var}} 占位符必须属于受支持变量。"""
    for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", text):
        var = m.group(1)
        if var not in IMAGE_PROMPT_VARS:
            raise ImagePromptTemplateError(
                f"模板包含不支持的变量：{{{{{var}}}}}。"
                f"支持的变量：{sorted(IMAGE_PROMPT_VARS)}"
            )


def render_image_prompt_template(text: str, values: dict[str, str]) -> str:
    """替换模板变量；预览未提供版式时使用清晰占位说明，未知变量仍保留。"""
    effective_values = {**_RENDER_DEFAULTS, **values}
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: effective_values.get(m.group(1), m.group(0)),
        text,
    )
