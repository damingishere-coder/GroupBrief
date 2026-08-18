"""生图 Prompt 模板服务。

模板以 UTF-8 文本文件存储在 templates/image_prompt/ 下（默认 default.md）。
与排行榜模板（app/ranking/template_service.py）结构一致，变量不同：
group_name / period_start / period_end / message_count / speaker_count。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config.settings import PROJECT_ROOT

# 默认生图 Prompt 模板（与 templates/image_prompt/default.md 同步；恢复默认时写回此内容）
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
（幽默有趣，可结合当天梗；必须来自真实聊天）

【副标题】
（一句话点出当天核心）

【整体视觉】
竖版海报，蓝白主色调，漫画信息图风格，顶部大标题，中部按事件分区，
底部数据条。画面明快、留白充足、中文大字排版。

【版面1】~【版面N】
每个版面包含：标题 / 事件 / 代表人物 / 建议画面 / 可用文字
选取 5~8 个主要话题（来自真实聊天事件）

【底部总结】
一句话文案

【硬性要求】
1. 只使用聊天内容中真实存在的事件、人物、对话，禁止编造。
2. 不得凭空补充金额、时间、地点、身份关系。
3. 原话引用必须来自真实聊天，可适当缩写，但不能改写事实。
4. 可以幽默化标题，但不能改变事实。
5. 海报人物依据「聊天事件中提到的人物」，而不是发言排行榜 Top10。
6. 数据（消息数、发言人数）必须使用给定数字，禁止自行计算。
"""

# 生图 Prompt 模板支持的变量
IMAGE_PROMPT_VARS = frozenset(
    {"group_name", "period_start", "period_end", "message_count", "speaker_count"}
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


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
    """替换模板变量。未知占位符保留原样（由调用方决定是否校验）。"""
    return re.sub(
        r"\{\{\s*(\w+)\s*\}\}",
        lambda m: values.get(m.group(1), m.group(0)),
        text,
    )
