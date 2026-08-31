from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.api import reports as reports_api
from app.api import runs as runs_api
from app.api import system as system_api
from app.config.settings import Settings
from app.db import repository as repo
from app.db.models import Group, GroupRun, Report, Run
from app.db.offline_migrations import MIGRATION_CHECKSUM, MIGRATION_ID
from app.services.email_service import EmailService


@pytest.fixture(autouse=True)
def _restore_repository_engine():
    """本文件会切换全局引擎；每条用例结束后恢复会话测试库。"""
    original_engine = repo.engine
    yield
    current_engine = repo.engine
    if current_engine is not None and current_engine is not original_engine:
        current_engine.dispose()
    repo.engine = original_engine


def _settings(path: Path) -> Settings:
    return Settings(database_url=f"sqlite:///{path.as_posix()}", _env_file=None)


def test_fresh_database_gets_current_schema_and_enforces_foreign_keys(tmp_path: Path) -> None:
    database = tmp_path / "fresh.db"
    engine = repo.init_db(_settings(database))
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 1
            migration = connection.exec_driver_sql(
                "SELECT checksum FROM schema_migrations WHERE migration_id=?",
                (MIGRATION_ID,),
            ).one()
            assert migration[0] == MIGRATION_CHECKSUM
            columns = {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(group_runs)")
            }
            assert {"legacy_group_id", "identity_state", "orphan_reason"}.issubset(columns)
            group_indexes = {
                row[1]: bool(row[2])
                for row in connection.exec_driver_sql("PRAGMA index_list(groups)")
            }
            assert group_indexes["uq_groups_wechat_group_id_active"] is True
            run_indexes = {
                row[1] for row in connection.exec_driver_sql("PRAGMA index_list(runs)")
            }
            assert "ix_runs_report_date_status" in run_indexes

        with Session(engine) as session:
            group = Group(display_name="活动群", wechat_group_id="active@chatroom")
            run = Run(report_date="2026-08-24", status="success")
            session.add(group)
            session.add(run)
            session.commit()
            session.refresh(group)
            session.refresh(run)
            group_run = GroupRun(run_id=run.id, group_id=group.id)
            session.add(group_run)
            session.commit()
            session.refresh(group_run)
            session.add(Report(group_run_id=group_run.id, ranking_text="排行榜"))
            session.commit()

            session.delete(group)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        # 重复初始化当前 Schema 必须是只读兼容检查，而不是重复迁移。
        second_engine = repo.init_db(_settings(database))
        second_engine.dispose()
    finally:
        engine.dispose()


def test_legacy_database_fails_closed_with_migration_guidance(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE group_runs (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                provider_used VARCHAR NOT NULL,
                message_count INTEGER NOT NULL,
                speaker_count INTEGER NOT NULL,
                ranking_status VARCHAR NOT NULL,
                prompt_status VARCHAR NOT NULL,
                error_message VARCHAR NOT NULL
            )
            """
        )

    with pytest.raises(repo.DatabaseSchemaError, match="scripts/migrate_db.py"):
        repo.init_db(_settings(database))


def test_orphaned_history_is_visible_but_excluded_from_active_stats_and_email(
    tmp_path: Path,
) -> None:
    engine = repo.init_db(_settings(tmp_path / "orphaned.db"))
    try:
        with Session(engine) as session:
            group = Group(display_name="当前群", wechat_group_id="current@chatroom")
            run = Run(report_date="2026-08-24", status="success")
            session.add(group)
            session.add(run)
            session.commit()
            session.refresh(group)
            session.refresh(run)

            linked = GroupRun(
                run_id=run.id,
                group_id=group.id,
                message_count=10,
                speaker_count=3,
                ranking_status="success",
                prompt_status="success",
            )
            orphaned = GroupRun(
                run_id=run.id,
                group_id=None,
                legacy_group_id=77,
                identity_state="orphaned",
                orphan_reason="historical_group_missing",
                message_count=90,
                speaker_count=30,
                ranking_status="success",
                prompt_status="success",
            )
            session.add(linked)
            session.add(orphaned)
            session.commit()
            session.refresh(linked)
            session.refresh(orphaned)
            session.add(Report(group_run_id=linked.id, ranking_text="当前群排行榜"))
            session.add(Report(group_run_id=orphaned.id, ranking_text="历史群排行榜"))
            session.commit()

            detail = runs_api.run_detail(run.id, session)
            by_state = {row["identity_state"]: row for row in detail["group_runs"]}
            assert by_state["orphaned"]["group_id"] is None
            assert by_state["orphaned"]["legacy_group_id"] == 77
            assert by_state["orphaned"]["group_name"] == "历史群（旧 ID 77）"
            assert "None" not in by_state["orphaned"]["group_name"]

            latest = reports_api.latest(session)
            orphan_report = next(row for row in latest if row["identity_state"] == "orphaned")
            assert orphan_report["group_id"] is None
            assert orphan_report["legacy_group_id"] == 77

            stats = system_api.stats(session)
            assert stats["total_messages"] == 10
            assert stats["total_speakers"] == 3

            email = EmailService(_settings(tmp_path / "unused.db")).build_email(session, run)
            assert [block.group_name for block in email.blocks] == ["当前群"]
            assert any("旧 ID 77" in item and "不发送" in item for item in email.missing)
    finally:
        engine.dispose()
