from __future__ import annotations

from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from app.db import repository as repo
from app.db.models import Group, Setting


def test_group_prompt_and_wechat_columns_migrate_idempotently_with_safe_defaults(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE groups (id INTEGER PRIMARY KEY, display_name VARCHAR NOT NULL)")
        connection.exec_driver_sql("INSERT INTO groups(id, display_name) VALUES (1, '旧群')")
    monkeypatch.setattr(repo, "engine", engine)

    repo._migrate_group_v2_columns()
    repo._migrate_group_v2_columns()

    with engine.connect() as connection:
        columns = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(groups)")]
        row = connection.exec_driver_sql(
            "SELECT image_prompt_override, wechat_send_enabled, deleted_at FROM groups WHERE id = 1"
        ).one()
    assert columns.count("image_prompt_override") == 1
    assert columns.count("wechat_send_enabled") == 1
    assert columns.count("deleted_at") == 1
    assert row[0] == ""
    assert bool(row[1]) is False
    assert row[2] is None


def test_group_queries_hide_deleted_but_keep_history_lookup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'soft-delete.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)

    with Session(engine) as session:
        active = repo.save_group(session, Group(display_name="当前群", wechat_group_id="active@chatroom"))
        active_id = active.id
        deleted = repo.save_group(session, Group(display_name="回收站群", wechat_group_id="deleted@chatroom"))
        deleted_id = deleted.id
        repo.delete_group(session, deleted_id)

    with Session(engine) as session:
        assert [group.id for group in repo.list_groups(session)] == [active_id]
        assert [group.id for group in repo.list_groups(session, only_enabled=True)] == [active_id]
        assert [group.id for group in repo.list_groups(session, include_deleted=True)] == [active_id, deleted_id]
        assert repo.get_active_group(session, deleted_id) is None
        historical = repo.get_group(session, deleted_id)
        assert historical is not None
        assert historical.deleted_at is not None
        assert historical.enabled is False
        assert historical.wechat_send_enabled is False


def test_five_legacy_defaults_migrate_once_without_overwriting_custom(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'themes.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE groups (id INTEGER PRIMARY KEY, display_name VARCHAR NOT NULL)")
        for index in range(1, 7):
            connection.exec_driver_sql(
                "INSERT INTO groups(id, display_name) VALUES (?, ?)",
                (index, f"旧群{index}"),
            )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)
    repo._migrate_group_v2_columns()
    with Session(engine) as session:
        session.add(Setting(key="ai_model", value="deepseek-chat"))
        session.add(Setting(key="max_context_chars", value="12000"))
        session.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE groups SET image_theme='blue_white', image_theme_custom='指定手账' WHERE id=6"
        )

    repo._migrate_parallel_summary_defaults()
    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, image_theme, image_theme_custom FROM groups ORDER BY id"
        ).all()
        assert [row[1] for row in rows[:5]] == ["random_preset"] * 5
        assert tuple(rows[5][1:]) == ("blue_white", "指定手账")
        connection.exec_driver_sql("UPDATE groups SET image_theme='blue_white' WHERE id=1")
    with Session(engine) as session:
        assert session.get(Setting, "migration_daily_random_theme_v1").value == "done"
        assert session.get(Setting, "ai_model").value == "deepseek-v4-flash"
        assert session.get(Setting, "max_context_chars").value == "50000"
        session.get(Setting, "max_context_chars").value = ""
        session.commit()
    repo._migrate_parallel_summary_defaults()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT image_theme FROM groups WHERE id=1").scalar_one() == "blue_white"
    with Session(engine) as session:
        assert session.get(Setting, "max_context_chars").value == "50000"


def test_codex_summary_defaults_migrate_once_and_keep_fallback_model(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'codex-summary.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)

    with Session(engine) as session:
        session.add(Group(display_name="旧默认", summary_model="deepseek-v4-flash", prompt_model="deepseek-chat"))
        session.add(Group(display_name="自定义", summary_model="custom-model", prompt_model="custom-model"))
        session.add(Setting(key="ai_model", value="deepseek-v4-flash"))
        session.commit()

    repo._migrate_codex_summary_defaults()

    with Session(engine) as session:
        groups = list(session.exec(select(Group).order_by(Group.id)).all())
        assert (groups[0].summary_model, groups[0].prompt_model) == ("gpt-5.6-sol", "gpt-5.6-sol")
        assert (groups[1].summary_model, groups[1].prompt_model) == ("custom-model", "custom-model")
        assert session.get(Setting, "ai_model").value == "deepseek-v4-flash"
        assert session.get(Setting, "migration_codex_summary_primary_v1").value == "done"
        groups[0].summary_model = "deepseek-v4-flash"
        session.add(groups[0])
        session.commit()

    repo._migrate_codex_summary_defaults()
    with Session(engine) as session:
        assert session.exec(select(Group).where(Group.display_name == "旧默认")).one().summary_model == "deepseek-v4-flash"


def test_daily_schedule_defaults_migrate_once_without_overwriting_custom(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'daily-schedule.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(repo, "engine", engine)

    with Session(engine) as session:
        session.add(Setting(key="schedule_generate_time", value="00:01"))
        session.add(Group(display_name="旧默认群", wechat_group_name="旧默认群", send_time="00:01"))
        session.add(Group(display_name="自定义群", wechat_group_name="自定义群", send_time="09:15"))
        session.commit()

    repo._migrate_daily_schedule_defaults()
    repo._migrate_daily_schedule_0015()

    with Session(engine) as session:
        groups = list(session.exec(select(Group).order_by(Group.id)).all())
        assert session.get(Setting, "schedule_generate_time").value == "00:15"
        assert session.get(Setting, "migration_daily_schedule_0030_v1").value == "done"
        assert session.get(Setting, "migration_daily_schedule_0015_v2").value == "done"
        assert [group.send_time for group in groups] == ["08:30", "09:15"]

        # 模拟用户在迁移后主动改回 00:01；再次启动不得覆盖。
        session.get(Setting, "schedule_generate_time").value = "00:01"
        groups[0].send_time = "00:01"
        session.add(groups[0])
        session.commit()

    repo._migrate_daily_schedule_defaults()
    repo._migrate_daily_schedule_0015()

    with Session(engine) as session:
        assert session.get(Setting, "schedule_generate_time").value == "00:01"
        assert session.exec(select(Group).where(Group.display_name == "旧默认群")).one().send_time == "00:01"
