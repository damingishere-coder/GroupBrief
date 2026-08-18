"""数据库初始化和 Repository。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, create_engine, select

from app.config.settings import Settings
from app.db.models import Group, Report, Run, Setting

engine: Any = None


# V2 群配置扩展列（幂等迁移：仅在列不存在时 ALTER TABLE ADD COLUMN）
_V2_GROUP_COLUMNS: dict[str, str] = {
    "schedule_rule": "VARCHAR(64) NOT NULL DEFAULT 'weekday_default'",
    "send_time": "VARCHAR(8) NOT NULL DEFAULT '08:30'",
    "summary_model": "VARCHAR(64) NOT NULL DEFAULT 'deepseek-v4-flash'",
    "prompt_model": "VARCHAR(64) NOT NULL DEFAULT 'deepseek-v4-flash'",
    "image_enabled": "BOOLEAN NOT NULL DEFAULT 1",
    "send_target": "VARCHAR(256) NOT NULL DEFAULT ''",
    "ranking_template": "VARCHAR(64) NOT NULL DEFAULT 'default'",
    "image_prompt_template": "VARCHAR(64) NOT NULL DEFAULT 'default'",
}


def _migrate_group_v2_columns() -> None:
    """为已存在的 groups 表补 V2 列（幂等）。"""
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(groups)")}
        for col, ddl in _V2_GROUP_COLUMNS.items():
            if col not in existing:
                conn.exec_driver_sql(f"ALTER TABLE groups ADD COLUMN {col} {ddl}")
        conn.commit()


def init_db(settings: Settings) -> Any:
    """初始化 SQLite 引擎并建表。"""
    global engine
    db_path: Path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    _migrate_group_v2_columns()
    _seed_defaults(settings)
    apply_db_settings(settings)
    return engine


def _seed_defaults(settings: Settings) -> None:
    """写入默认设置项（不覆盖已存在值）。"""
    defaults = {
        "history_provider_primary": settings.history_provider_primary,
        "history_provider_fallback": settings.history_provider_fallback,
        "history_provider_mock_enabled": str(settings.history_provider_mock_enabled),
        "ai_provider": settings.ai_provider,
        "ai_base_url": settings.ai_base_url,
        "ai_model": settings.ai_model,
        "email_enabled": str(settings.email_enabled),
        "email_recipient": settings.email_recipient,
        "email_send_partial_report": str(settings.email_send_partial_report),
        "schedule_generate_time": settings.schedule_generate_time,
        "schedule_email_time": settings.schedule_email_time,
    }
    with Session(engine) as session:
        for key, value in defaults.items():
            existing = session.get(Setting, key)
            if existing is None:
                session.add(Setting(key=key, value=str(value)))
        session.commit()


# 环境相关字段：若对应环境变量被显式设置，则环境变量优先于数据库。
# 用于 Docker 容器（MCP 需指向 host.docker.internal）与宿主机直跑共存。
_ENV_PRIORITY_FIELDS = {
    "wechat_mcp_url": "WECHAT_MCP_URL",
    "wechat_mcp_allowed_hosts": "WECHAT_MCP_ALLOWED_HOSTS",
}


def apply_db_settings(settings: Settings) -> list[str]:
    """把数据库中已保存的设置应用到 Settings 运行实例。

    数据库只在用户通过设置 API 显式保存时写入（见 app/api/settings.py），
    因此未来启动时数据库值优先于 .env；掩码敏感值（"******"）与凭据
    不会被写入数据库，也不会覆盖运行时非空值。

    例外：_ENV_PRIORITY_FIELDS（如 WECHAT_MCP_URL）若环境变量显式设置，
    则跳过数据库值（Docker 容器环境与宿主机直跑共用同一数据库）。
    """
    import os

    with Session(engine) as session:
        rows = session.exec(select(Setting)).all()
        values = {s.key: s.value for s in rows}
    for field, env_name in _ENV_PRIORITY_FIELDS.items():
        if env_name in os.environ:
            values.pop(field, None)
    return settings.apply_runtime_values(values)


def get_session():
    with Session(engine) as session:
        yield session


# ---------- Groups ----------

def list_groups(session: Session, only_enabled: bool = False) -> list[Group]:
    stmt = select(Group).order_by(Group.id)
    groups = session.exec(stmt).all()
    if only_enabled:
        groups = [g for g in groups if g.enabled]
    return list(groups)


def get_group(session: Session, group_id: int) -> Group | None:
    return session.get(Group, group_id)


def save_group(session: Session, group: Group) -> Group:
    group.updated_at = _now()
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def delete_group(session: Session, group_id: int) -> None:
    group = session.get(Group, group_id)
    if group:
        session.delete(group)
        session.commit()


# ---------- Runs ----------

def create_run(session: Session, run: Run) -> Run:
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def update_run(session: Session, run: Run) -> None:
    session.add(run)
    session.commit()


def get_run(session: Session, run_id: int) -> Run | None:
    return session.get(Run, run_id)


def find_runs(session: Session, limit: int = 50) -> list[Run]:
    stmt = select(Run).order_by(Run.id.desc()).limit(limit)
    return list(session.exec(stmt).all())


# ---------- Reports ----------

def save_report(session: Session, report: Report) -> Report:
    report.updated_at = _now()
    session.add(report)
    session.commit()
    session.refresh(report)
    return report


def get_report_by_group_run(session: Session, group_run_id: int) -> Report | None:
    stmt = select(Report).where(Report.group_run_id == group_run_id)
    return session.exec(stmt).first()


def find_recent_reports(session: Session, limit: int = 20) -> list[Report]:
    stmt = select(Report).order_by(Report.id.desc()).limit(limit)
    return list(session.exec(stmt).all())


def get_setting_value(session: Session, key: str, default: str = "") -> str:
    setting = session.get(Setting, key)
    return setting.value if setting else default


def set_setting_value(session: Session, key: str, value: str) -> None:
    setting = session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=str(value))
    else:
        setting.value = str(value)
    session.add(setting)
    session.commit()


def _now() -> Any:
    from datetime import datetime

    return datetime.now()
