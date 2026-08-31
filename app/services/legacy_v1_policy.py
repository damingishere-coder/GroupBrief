"""旧 V1 数据库流水线的集中冻结策略。"""

from __future__ import annotations

from app.config.settings import Settings
from app.core.logging import get_logger

logger = get_logger("groupbrief.legacy_v1")

LEGACY_V1_WRITE_BLOCKED = "LEGACY_V1_WRITE_BLOCKED"


class LegacyV1WriteBlockedError(RuntimeError):
    def __init__(self, operation: str, replacement: str):
        self.operation = operation
        self.replacement = replacement
        super().__init__(
            f"旧 V1 写入已冻结：{operation}；请使用 {replacement}"
        )

    def __str__(self) -> str:
        return str(self.args[0])

    def as_detail(self) -> dict[str, str]:
        return {
            "code": LEGACY_V1_WRITE_BLOCKED,
            "message": str(self),
            "operation": self.operation,
            "replacement": self.replacement,
        }


def require_legacy_v1_write(
    settings: Settings,
    *,
    operation: str,
    replacement: str,
) -> None:
    """只允许环境级 maintenance 模式执行旧写入。"""
    if settings.legacy_v1_write_mode == "maintenance":
        logger.warning(
            "旧 V1 maintenance 写入已启用：operation=%s replacement=%s",
            operation,
            replacement,
        )
        return
    logger.warning(
        "旧 V1 写入已阻断：operation=%s replacement=%s",
        operation,
        replacement,
    )
    raise LegacyV1WriteBlockedError(operation, replacement)
