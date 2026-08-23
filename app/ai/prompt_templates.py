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
正常保留 5～7 个独立话题的密度，用漫画镜头表现“谁做了什么、别人怎样接话”，不要画成栏目列表。

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

【漫画分镜】
{{layout_name}}
{{layout_instruction}}

【主标题】
（优先使用群聊原句、群内梗、反差或回环；建议不超过 18 个汉字）

【副标题】
（一句话概括当天讨论；建议不超过 26 个汉字）

【剧情与镜头分配】
按给定阅读顺序使用全部入选话题，不得遗漏、重复、增删或改选。
一个话题不等于一个矩形模块；同一话题可以用连续的环境、动作、对白、反应或特写镜头展开。
正常 5～7 个话题应形成 7～12 个视觉格，至少一个话题使用两个以上连续镜头。

【画面文字白名单】
只清晰绘制：主标题、统计日期、给定数据、自然的话题短标题、短事实旁白、真实姓名和精选群聊气泡。
不得绘制程序字段、主题编号、说明性栏目名或 JSON；空间不足时先减少装饰和副标题，保留事实与气泡。

【分镜表现】
整页至少有大、中、小三级格子尺寸差；使用嵌套反应小格、连续动作、局部特写或一次跨格主体建立节奏。
气泡尾巴、人物视线和动作线共同引导从上到下、从左到右阅读；禁止整齐两列等高矩形和重复模板块。

【底部总结】
可用一句短文案回收当天讨论；不使用“信息量拉满”“一天顶一周”“比过山车还刺激”等通用套话。

【硬性要求】
1. 只使用聊天内容中真实存在的事件、人物、对话，禁止编造。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 气泡文字必须来自程序给定的真实聊天，可缩短长度，但不能改写事实。
4. 可以使用字面化、反差、回环、误会与反转、一本正经地荒诞，但不能改变事实。
5. 海报人物依据聊天事件中的真实人员，而不是发言排行榜 Top10；姓名只能使用程序回查得到的人员。
6. 数据（消息数、发言人数）必须使用给定数字，禁止自行计算。
7. 【大主题】是全图最高视觉约束，控制配色、画材、造型、装饰、纹理、光影和画风；【漫画分镜】不得替换或削弱它。
8. 【漫画分镜】只控制格子几何、阅读路径和镜头节拍；每张图只能使用给定的一种骨架。
9. 不得把法庭、菜单、地图、新闻台等无关主题包装强加给真实聊天。
10. 最终 Prompt 必须严格包含给定的 2～7 个入选主题且各使用一次；证据不足时由上游减少数量，不得编造。
11. 每个入选话题至少显示一个真实姓名、一句事实短句和一句给定气泡，不得用泛化头像替代人物。
12. 漫画主体与对话必须和对应聊天事实直接相关，视觉比喻只能放大已有笑点，不能另写故事。
13. 必须把“统计日期：{{report_date}}”逐字作为清晰可见的画面文字，放在海报顶部或底部。
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
    "layout_name": "（生成时自动选择漫画分镜骨架）",
    "layout_instruction": "（生成时根据入选主题和最近分镜历史写入大小格与镜头节拍）",
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
