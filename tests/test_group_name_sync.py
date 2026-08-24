from __future__ import annotations

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.data_sources.base import DataSourceHealth, DataSourceStatus, ResolvedGroup, WeChatDataSource
from app.db import repository as repo
from app.db.models import Group
from app.services.group_name_sync import (
    GroupNameSyncService,
    effective_send_target,
    send_target_mode,
)


class StubSource(WeChatDataSource):
    name = "wechat_data_analysis"

    def __init__(self, groups: list[ResolvedGroup], *, available: bool = True):
        self.groups = groups
        self.available = available

    def health_check(self) -> DataSourceHealth:
        if self.available:
            return DataSourceHealth(DataSourceStatus.OK, "ok")
        return DataSourceHealth(DataSourceStatus.UNAVAILABLE, "本地数据源不可用")

    def list_groups(self) -> list[ResolvedGroup]:
        return self.groups

    def resolve_group(self, group_name: str) -> list[ResolvedGroup]:
        return []

    def fetch_messages(self, group_id, start_time, end_time):  # pragma: no cover - 同步服务不会取消息
        raise AssertionError("群名同步不得读取聊天消息")


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'group-name-sync.db'}")
    SQLModel.metadata.create_all(engine)
    return engine


def test_sync_updates_current_name_by_stable_id_and_preserves_archive_and_override(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr(repo, "engine", engine)
    with Session(engine) as session:
        auto = repo.save_group(
            session,
            Group(
                display_name="固定归档名",
                wechat_group_id="auto@chatroom",
                wechat_group_name="旧群名",
                send_target="",
            ),
        )
        manual = repo.save_group(
            session,
            Group(
                display_name="人工目标归档",
                wechat_group_id="manual@chatroom",
                wechat_group_name="旧人工群名",
                send_target="人工搜索目标",
            ),
        )
        unchanged = repo.save_group(
            session,
            Group(
                display_name="不变归档",
                wechat_group_id="same@chatroom",
                wechat_group_name="不变群名",
            ),
        )
        invalid = repo.save_group(
            session,
            Group(display_name="无效归档", wechat_group_id="invalid@chatroom", wechat_group_name="缓存名"),
        )
        conflict = repo.save_group(
            session,
            Group(display_name="冲突归档", wechat_group_id="conflict@chatroom", wechat_group_name="缓存冲突名"),
        )
        missing = repo.save_group(
            session,
            Group(display_name="缺失归档", wechat_group_id="missing@chatroom", wechat_group_name="缓存缺失名"),
        )
        auto_id = int(auto.id)
        manual_id = int(manual.id)
        unchanged_id = int(unchanged.id)
        invalid_id = int(invalid.id)
        conflict_id = int(conflict.id)
        missing_id = int(missing.id)

        source = StubSource(
            [
                ResolvedGroup("auto@chatroom", "新群名"),
                ResolvedGroup("manual@chatroom", "新人工群名"),
                ResolvedGroup("same@chatroom", "不变群名"),
                ResolvedGroup("invalid@chatroom", "invalid@chatroom"),
                ResolvedGroup("conflict@chatroom", "冲突名称一"),
                ResolvedGroup("conflict@chatroom", "冲突名称二"),
            ]
        )
        report = GroupNameSyncService(source).sync(session)

    assert report.status == "partial"
    assert report.checked == 6
    assert report.unchanged == 1
    assert {item["id"] for item in report.updated} == {auto_id, manual_id}
    assert {item["reason"] for item in report.skipped} == {
        "invalid_name",
        "conflicting_names",
        "not_found",
    }
    assert report.is_fresh(auto_id)
    assert report.is_fresh(manual_id)
    assert report.is_fresh(unchanged_id)
    assert not report.is_fresh(invalid_id)
    assert not report.is_fresh(conflict_id)
    assert not report.is_fresh(missing_id)

    with Session(engine) as session:
        saved_auto = session.exec(select(Group).where(Group.id == auto_id)).one()
        saved_manual = session.exec(select(Group).where(Group.id == manual_id)).one()
        assert saved_auto.display_name == "固定归档名"
        assert saved_auto.wechat_group_name == "新群名"
        assert saved_auto.send_target == ""
        assert effective_send_target(saved_auto) == "新群名"
        assert send_target_mode(saved_auto) == "auto"
        assert saved_manual.display_name == "人工目标归档"
        assert saved_manual.wechat_group_name == "新人工群名"
        assert saved_manual.send_target == "人工搜索目标"
        assert effective_send_target(saved_manual) == "人工搜索目标"
        assert send_target_mode(saved_manual) == "manual"


def test_sync_unavailable_preserves_cached_name_and_target(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    monkeypatch.setattr(repo, "engine", engine)
    with Session(engine) as session:
        group = repo.save_group(
            session,
            Group(
                display_name="固定归档",
                wechat_group_id="cached@chatroom",
                wechat_group_name="缓存微信群名",
                send_target="",
            ),
        )
        group_id = int(group.id)
        report = GroupNameSyncService(StubSource([], available=False)).sync(session)

    assert report.status == "unavailable"
    assert report.checked == 1
    assert not report.is_fresh(group_id)
    assert report.skipped == [
        {"id": group_id, "wechat_group_id": "cached@chatroom", "reason": "source_unavailable"}
    ]
    with Session(engine) as session:
        saved = session.get(Group, group_id)
        assert saved.wechat_group_name == "缓存微信群名"
        assert effective_send_target(saved) == "缓存微信群名"
