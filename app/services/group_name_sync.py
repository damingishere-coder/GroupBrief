"""按稳定微信群 ID 同步当前名称，并保留人工发送目标覆盖。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from app.core.logging import get_logger
from app.data_sources.base import WeChatDataSource
from app.db import repository as repo
from app.db.models import Group

logger = get_logger("groupbrief.group_name_sync")


def effective_send_target(group: Group) -> str:
    """返回真正用于微信搜索的名称；非空 send_target 仅代表人工覆盖。"""
    return (
        str(group.send_target or "").strip()
        or str(group.wechat_group_name or "").strip()
        or str(group.display_name or "").strip()
    )


def send_target_mode(group: Group) -> str:
    return "manual" if str(group.send_target or "").strip() else "auto"


@dataclass
class GroupNameSyncReport:
    status: str
    source: str
    checked: int
    unchanged: int = 0
    updated: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    fresh_group_ids: set[int] = field(default_factory=set, repr=False)
    synced_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "source": self.source,
            "checked": self.checked,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "synced_at": self.synced_at,
            "detail": self.detail,
        }

    def is_fresh(self, group_id: int | None) -> bool:
        return group_id is not None and group_id in self.fresh_group_ids


class GroupNameSyncService:
    """从真实数据源取一次群列表，再按稳定 ID 批量更新本地当前名称。"""

    def __init__(self, data_source: WeChatDataSource):
        self.data_source = data_source

    def sync(
        self,
        session: Session,
        *,
        group_ids: list[int] | None = None,
    ) -> GroupNameSyncReport:
        groups = repo.list_groups(session)
        if group_ids is not None:
            wanted = {int(group_id) for group_id in group_ids}
            groups = [group for group in groups if group.id in wanted]
        groups = [group for group in groups if str(group.wechat_group_id or "").strip()]
        source = str(getattr(self.data_source, "name", "wechat_data_analysis") or "wechat_data_analysis")
        report = GroupNameSyncReport(status="ok", source=source, checked=len(groups))
        if not groups:
            return report

        try:
            health = self.data_source.health_check()
        except Exception as exc:
            return self._unavailable(report, groups, f"数据源健康检查异常：{type(exc).__name__}")
        if not health.ok:
            detail = str(health.detail or "数据源不可用")[:300]
            return self._unavailable(report, groups, detail)

        try:
            discovered = self.data_source.list_groups()
        except Exception as exc:
            return self._unavailable(report, groups, f"群列表读取异常：{type(exc).__name__}")
        if not discovered:
            return self._unavailable(report, groups, "数据源没有返回可核验的群列表")

        names_by_id: dict[str, set[str]] = {}
        invalid_ids: set[str] = set()
        for candidate in discovered:
            wechat_id = str(candidate.group_id or "").strip()
            if not wechat_id:
                continue
            name = str(candidate.group_name or "").strip()
            if not _valid_current_name(name, wechat_id):
                invalid_ids.add(wechat_id)
                continue
            names_by_id.setdefault(wechat_id, set()).add(name)

        changed_groups: list[Group] = []
        for group in groups:
            local_id = int(group.id) if group.id is not None else None
            wechat_id = str(group.wechat_group_id or "").strip()
            names = names_by_id.get(wechat_id, set())
            if not names:
                reason = "invalid_name" if wechat_id in invalid_ids else "not_found"
                report.skipped.append(_skipped(group, reason))
                continue
            if len(names) != 1:
                report.skipped.append(_skipped(group, "conflicting_names"))
                continue

            current_name = next(iter(names))
            old_name = str(group.wechat_group_name or "").strip()
            if local_id is not None:
                report.fresh_group_ids.add(local_id)
            if current_name == old_name:
                report.unchanged += 1
                continue

            group.wechat_group_name = current_name
            group.updated_at = datetime.now()
            session.add(group)
            changed_groups.append(group)
            report.updated.append(
                {
                    "id": local_id,
                    "wechat_group_id": wechat_id,
                    "old_name": old_name,
                    "new_name": current_name,
                }
            )

        if changed_groups:
            session.commit()
        if report.skipped:
            report.status = "partial"
        logger.info(
            "微信群名同步完成：source=%s checked=%d updated=%d unchanged=%d skipped=%d",
            report.source,
            report.checked,
            len(report.updated),
            report.unchanged,
            len(report.skipped),
        )
        return report

    @staticmethod
    def _unavailable(
        report: GroupNameSyncReport,
        groups: list[Group],
        detail: str,
    ) -> GroupNameSyncReport:
        report.status = "unavailable"
        report.detail = detail
        report.skipped = [_skipped(group, "source_unavailable") for group in groups]
        logger.warning("微信群名同步不可用：source=%s detail=%s", report.source, detail)
        return report


def _valid_current_name(name: str, wechat_group_id: str) -> bool:
    if not name or len(name) > 256:
        return False
    if name.casefold() == wechat_group_id.casefold() or name.casefold().endswith("@chatroom"):
        return False
    return not any(char in name for char in ("\x00", "\r", "\n"))


def _skipped(group: Group, reason: str) -> dict:
    return {
        "id": int(group.id) if group.id is not None else None,
        "wechat_group_id": str(group.wechat_group_id or "").strip(),
        "reason": reason,
    }
