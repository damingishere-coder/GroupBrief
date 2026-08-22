"""排行榜模板管理服务。

模板以 UTF-8 文本文件存储在 templates/ranking/ 下，文件名即模板名。
- 默认模板内容固化在 DEFAULT_RANKING_TEMPLATE（代码内），可随时恢复；
- 模板名仅允许安全字符（字母数字 _ -），防止路径穿越；
- 保存时校验模板变量（未支持变量给出明确错误）；
- default 模板不可删除。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config.settings import PROJECT_ROOT

# 默认模板内容（对应路线文档默认格式；用户可改文件，恢复默认即写回此内容）。
# 注意：群名 {{group_name}} 原样渲染（真实群名可能自带 emoji，如「茶馆V3.0（三周年纪念）🐮🐴」），
# 模板不再硬编码装饰 emoji，避免出现「🐮🐴🐮🐴」重复；如需装饰请在模板中心自行编辑。
DEFAULT_RANKING_TEMPLATE = """===== {{group_name}} =====

【发言排行榜】

{{group_name}}
消息统计
------------

时间起：{{period_start}}
时间止：{{period_end}}

------------

发言人数：{{speaker_count}}

总消息：{{message_count}}

------------

发言 Top{{top_limit}}
{{top_lines}}
"""

# 支持的模板变量
SUPPORTED_VARS = frozenset(
    {
        "group_name",
        "period_start",
        "period_end",
        "speaker_count",
        "message_count",
        "top_limit",
        "top_lines",
        "top10_lines",
    }
)

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TemplateError(ValueError):
    """模板内容/名称错误。"""


class RankingTemplateService:
    def __init__(self, templates_dir: Path | None = None):
        self.dir = templates_dir or (PROJECT_ROOT / "templates" / "ranking")
        self._ensure_default()

    # ---------- 内部 ----------

    def _ensure_default(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "default.txt"
        if not path.exists():
            path.write_text(DEFAULT_RANKING_TEMPLATE, encoding="utf-8")

    def _path(self, name: str) -> Path:
        if not name or not _SAFE_NAME_RE.match(name):
            raise TemplateError(f"非法模板名：{name!r}")
        path = self.dir / f"{name}.txt"
        if not path.exists():
            raise TemplateError(f"模板不存在：{name}")
        return path

    # ---------- 模板操作 ----------

    def list_templates(self) -> list[str]:
        self._ensure_default()
        return sorted(p.stem for p in self.dir.glob("*.txt"))

    def read(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def save(self, name: str, content: str) -> None:
        if not name or not _SAFE_NAME_RE.match(name):
            raise TemplateError(f"非法模板名：{name!r}")
        validate_template(content)
        (self.dir / f"{name}.txt").write_text(content, encoding="utf-8")

    def delete(self, name: str) -> None:
        if name == "default":
            raise TemplateError("默认模板不可删除")
        self._path(name).unlink()

    def reset(self, name: str = "default") -> str:
        """恢复默认模板内容。"""
        if name != "default":
            raise TemplateError("目前仅支持恢复默认模板")
        (self.dir / "default.txt").write_text(DEFAULT_RANKING_TEMPLATE, encoding="utf-8")
        return DEFAULT_RANKING_TEMPLATE


def validate_template(text: str) -> None:
    """校验模板：所有 {{var}} 占位符必须属于受支持变量。"""
    for m in re.finditer(r"\{\{\s*(\w+)\s*\}\}", text):
        var = m.group(1)
        if var not in SUPPORTED_VARS:
            raise TemplateError(
                f"模板包含不支持的变量：{{{{{var}}}}}。"
                f"支持的变量：{sorted(SUPPORTED_VARS)}"
            )
