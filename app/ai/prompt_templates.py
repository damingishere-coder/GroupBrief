"""生图 Prompt 模板服务。

模板以 UTF-8 文本文件存储在 templates/image_prompt/ 下（默认 default.md）。
与排行榜模板（app/ranking/template_service.py）结构一致。最终固定区块由
main_title / subtitle / overall_visual / panels / text_rules / footer_summary 等
结构化变量填入；历史变量仅保留解析兼容，不进入默认模板。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config.settings import PROJECT_ROOT

# 默认生图 Prompt 模板（与 templates/image_prompt/default.md 同步）。动态内容先经
# 结构化证据校验，再由代码填入，模型不能自行增删区块。
DEFAULT_IMAGE_PROMPT_TEMPLATE = """【任务】
生成一张竖版微信群日报漫画信息图。

【群名称】
{{group_name}}

【统计时间】
{{period_start}} ~ {{period_end}}

【数据】
{{message_count}} 条消息
{{speaker_count}} 人发言

【主标题】
{{main_title}}

【副标题】
{{subtitle}}

【整体视觉】
{{overall_visual}}

{{panels}}

【文字规则】
{{text_rules}}

【底部总结】
{{footer_summary}}
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
        "main_title",
        "subtitle",
        "overall_visual",
        "panels",
        "text_rules",
        "footer_summary",
    }
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_RENDER_DEFAULTS = {
    "layout_name": "（生成时自动选择漫画分镜骨架）",
    "layout_instruction": "（生成时根据入选主题和最近分镜历史写入大小格与镜头节拍）",
    "main_title": "（生成时填入当天真实主标题）",
    "subtitle": "（生成时填入当天真实副标题）",
    "overall_visual": "（生成时填入固定整体视觉与当前风格）",
    "panels": "【版面1】\n（生成时按真实入选话题填入）",
    "text_rules": "（生成时填入固定文字规则）",
    "footer_summary": "（生成时填入当天真实底部总结）",
}
_FIXED_TEMPLATE_HEADINGS = (
    "任务",
    "群名称",
    "统计时间",
    "数据",
    "主标题",
    "副标题",
    "整体视觉",
    "文字规则",
    "底部总结",
)
_FIXED_TEMPLATE_VARS = frozenset(
    {
        "group_name",
        "period_start",
        "period_end",
        "message_count",
        "speaker_count",
        "main_title",
        "subtitle",
        "overall_visual",
        "panels",
        "text_rules",
        "footer_summary",
    }
)


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
    """校验模板变量以及固定群聊漫画区块合同。"""
    used_vars: set[str] = set()
    for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", text):
        var = m.group(1)
        used_vars.add(var)
        if var not in IMAGE_PROMPT_VARS:
            raise ImagePromptTemplateError(
                f"模板包含不支持的变量：{{{{{var}}}}}。"
                f"支持的变量：{sorted(IMAGE_PROMPT_VARS)}"
            )
    headings = tuple(re.findall(r"(?m)^【([^\n】]+)】\s*$", text))
    if headings != _FIXED_TEMPLATE_HEADINGS:
        raise ImagePromptTemplateError(
            "模板区块必须严格为：" + " → ".join(_FIXED_TEMPLATE_HEADINGS)
        )
    missing = sorted(_FIXED_TEMPLATE_VARS - used_vars)
    if missing:
        raise ImagePromptTemplateError(
            "固定漫画模板缺少变量：" + ", ".join(f"{{{{{name}}}}}" for name in missing)
        )


def render_image_prompt_template(text: str, values: dict[str, str]) -> str:
    """替换模板变量；预览未提供版式时使用清晰占位说明，未知变量仍保留。"""
    effective_values = {**_RENDER_DEFAULTS, **values}
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: effective_values.get(m.group(1), m.group(0)),
        text,
    )
