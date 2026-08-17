"""数据库初始化和 Repository。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Session, SQLModel, create_engine, select

from app.config.settings import Settings
from app.db.models import Group, Report, Run, Setting

engine: Any = None


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
    _seed_defaults(settings)
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
