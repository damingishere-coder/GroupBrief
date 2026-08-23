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
    "summary_model": "VARCHAR(64) NOT NULL DEFAULT 'gpt-5.6-sol'",
    "prompt_model": "VARCHAR(64) NOT NULL DEFAULT 'gpt-5.6-sol'",
    "image_enabled": "BOOLEAN NOT NULL DEFAULT 1",
    "send_target": "VARCHAR(256) NOT NULL DEFAULT ''",
    "ranking_template": "VARCHAR(64) NOT NULL DEFAULT 'default'",
    "image_prompt_template": "VARCHAR(64) NOT NULL DEFAULT 'default'",
    "image_theme": "VARCHAR(64) NOT NULL DEFAULT 'random_preset'",
    "image_theme_custom": "VARCHAR(80) NOT NULL DEFAULT ''",
    "image_prompt_override": "TEXT NOT NULL DEFAULT ''",
    "wechat_send_enabled": "BOOLEAN NOT NULL DEFAULT 0",
    "deleted_at": "DATETIME NULL",
}


def _migrate_group_v2_columns() -> None:
    """为已存在的 groups 表补 V2 列（幂等）。"""
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(groups)")}
        for col, ddl in _V2_GROUP_COLUMNS.items():
            if col not in existing:
                conn.exec_driver_sql(f"ALTER TABLE groups ADD COLUMN {col} {ddl}")
        conn.commit()


def _migrate_parallel_summary_defaults() -> None:
    """一次性升级旧模型别名和旧蓝白默认值。

    主题迁移用 Setting 标记保证只执行一次。这样本次升级会把历史默认蓝白群
    切到每日随机，但用户以后主动选择 ``blue_white`` 时不会在重启后被覆盖。
    """
    marker = "migration_daily_random_theme_v1"
    with Session(engine) as session:
        model_setting = session.get(Setting, "ai_model")
        if model_setting is not None and model_setting.value.strip() == "deepseek-chat":
            model_setting.value = "deepseek-v4-flash"
            session.add(model_setting)

        context_setting = session.get(Setting, "max_context_chars")
        if context_setting is not None and context_setting.value.strip() in {"", "12000"}:
            context_setting.value = "50000"
            session.add(context_setting)

        session.exec(
            Group.__table__.update()
            .where(Group.summary_model == "deepseek-chat")
            .values(summary_model="deepseek-v4-flash")
        )
        session.exec(
            Group.__table__.update()
            .where(Group.prompt_model == "deepseek-chat")
            .values(prompt_model="deepseek-v4-flash")
        )

        migration = session.get(Setting, marker)
        if migration is None:
            session.exec(
                Group.__table__.update()
                .where(Group.image_theme == "blue_white")
                .where(Group.image_theme_custom == "")
                .values(image_theme="random_preset")
            )
            session.add(Setting(key=marker, value="done"))
        session.commit()


def _migrate_daily_schedule_defaults() -> None:
    """一次性把旧 00:01 生成/发送默认值升级为 00:30/08:30。

    迁移标记保证历史值只改一次；用户以后主动选择 00:01 时不会在重启后
    被再次覆盖。其他自定义生成时间和群发送时间始终保持不变。
    """
    marker = "migration_daily_schedule_0030_v1"
    with Session(engine) as session:
        if session.get(Setting, marker) is not None:
            return

        generate_setting = session.get(Setting, "schedule_generate_time")
        if generate_setting is not None and generate_setting.value.strip() == "00:01":
            generate_setting.value = "00:30"
            session.add(generate_setting)

        session.exec(
            Group.__table__.update()
            .where(Group.send_time == "00:01")
            .values(send_time="08:30")
        )
        session.add(Setting(key=marker, value="done"))
        session.commit()


def _migrate_daily_schedule_0015() -> None:
    """一次性把上一版默认生成时间 00:30 升级为 00:15。

    只迁移上一版的固定默认值；用户主动配置的其他时间保持不变。
    """
    marker = "migration_daily_schedule_0015_v2"
    with Session(engine) as session:
        if session.get(Setting, marker) is not None:
            return

        generate_setting = session.get(Setting, "schedule_generate_time")
        if generate_setting is not None and generate_setting.value.strip() == "00:30":
            generate_setting.value = "00:15"
            session.add(generate_setting)
        session.add(Setting(key=marker, value="done"))
        session.commit()


def _migrate_codex_summary_defaults() -> None:
    """一次性把仍使用历史 DeepSeek 默认值的群切换到 Codex GPT。"""
    marker = "migration_codex_summary_primary_v1"
    with Session(engine) as session:
        if session.get(Setting, marker) is not None:
            return
        legacy_models = ("deepseek-chat", "deepseek-v4-flash")
        session.exec(
            Group.__table__.update()
            .where(Group.summary_model.in_(legacy_models))
            .values(summary_model="gpt-5.6-sol")
        )
        session.exec(
            Group.__table__.update()
            .where(Group.prompt_model.in_(legacy_models))
            .values(prompt_model="gpt-5.6-sol")
        )
        session.add(Setting(key=marker, value="done"))
        session.commit()


def _migrate_codex_image_timeout_default() -> None:
    """一次性把旧 Codex 生图默认超时 600 秒升级为 1200 秒。

    只迁移项目历史默认值；用户主动设置的任何其他值都保持不变。迁移标记
    保证用户以后即使主动改回 600 秒，也不会在重启后再次被覆盖。
    """
    marker = "migration_codex_image_timeout_1200_v1"
    with Session(engine) as session:
        if session.get(Setting, marker) is not None:
            return
        timeout_setting = session.get(Setting, "codex_timeout_seconds")
        if timeout_setting is not None and timeout_setting.value.strip() == "600":
            timeout_setting.value = "1200"
            session.add(timeout_setting)
        session.add(Setting(key=marker, value="done"))
        session.commit()


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
    # 先种默认值再迁移，确保旧 .env 中的 deepseek-chat / 12000 也会升级。
    _migrate_parallel_summary_defaults()
    _migrate_codex_summary_defaults()
    _migrate_codex_image_timeout_default()
    _migrate_daily_schedule_defaults()
    _migrate_daily_schedule_0015()
    apply_db_settings(settings)
    return engine


def _seed_defaults(settings: Settings) -> None:
    """写入默认设置项（不覆盖已存在值）。"""
    defaults = {
        "history_provider_primary": settings.history_provider_primary,
        "history_provider_fallback": settings.history_provider_fallback,
        "history_provider_mock_enabled": str(settings.history_provider_mock_enabled),
        "summary_provider_primary": settings.summary_provider_primary,
        "summary_provider_fallback": settings.summary_provider_fallback,
        "codex_summary_model": settings.codex_summary_model,
        "codex_summary_timeout_seconds": str(settings.codex_summary_timeout_seconds),
        "codex_summary_max_retries": str(settings.codex_summary_max_retries),
        "codex_summary_request_concurrency": str(settings.codex_summary_request_concurrency),
        "ai_provider": settings.ai_provider,
        "ai_base_url": settings.ai_base_url,
        "ai_model": settings.ai_model,
        "max_context_chars": str(settings.max_context_chars),
        "generation_group_concurrency": str(settings.generation_group_concurrency),
        "wechat_fetch_total_timeout_seconds": str(settings.wechat_fetch_total_timeout_seconds),
        "wechat_fetch_concurrency": str(settings.wechat_fetch_concurrency),
        "ai_request_concurrency": str(settings.ai_request_concurrency),
        "codex_path": settings.codex_path,
        "codex_home": settings.codex_home,
        "codex_timeout_seconds": str(settings.codex_timeout_seconds),
        "codex_generated_images_dir": settings.codex_generated_images_dir,
        "wechat_sender_mode": settings.wechat_sender_mode,
        "wechat_native_action_delay_seconds": str(settings.wechat_native_action_delay_seconds),
        "wechat_native_mutex_timeout_seconds": str(settings.wechat_native_mutex_timeout_seconds),
        "wechat_send_claim_seconds": str(settings.wechat_send_claim_seconds),
        "wechat_late_send_window_minutes": str(settings.wechat_late_send_window_minutes),
        "email_enabled": str(settings.email_enabled),
        "email_recipient": settings.email_recipient,
        "email_send_partial_report": str(settings.email_send_partial_report),
        "schedule_generate_time": settings.schedule_generate_time,
        "schedule_email_time": settings.schedule_email_time,
        "schedule_startup_catchup_enabled": str(settings.schedule_startup_catchup_enabled),
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

def list_groups(
    session: Session,
    only_enabled: bool = False,
    *,
    include_deleted: bool = False,
) -> list[Group]:
    stmt = select(Group).order_by(Group.id)
    if not include_deleted:
        stmt = stmt.where(Group.deleted_at.is_(None))
    if only_enabled:
        stmt = stmt.where(Group.enabled.is_(True))
    groups = session.exec(stmt).all()
    return list(groups)


def get_group(session: Session, group_id: int) -> Group | None:
    return session.get(Group, group_id)


def get_active_group(session: Session, group_id: int) -> Group | None:
    group = session.get(Group, group_id)
    if group is None or group.deleted_at is not None:
        return None
    return group


def find_group_by_wechat_id(
    session: Session,
    wechat_group_id: str,
    *,
    include_deleted: bool = True,
) -> Group | None:
    value = (wechat_group_id or "").strip()
    if not value:
        return None
    stmt = select(Group).where(Group.wechat_group_id == value).order_by(Group.id)
    if not include_deleted:
        stmt = stmt.where(Group.deleted_at.is_(None))
    return session.exec(stmt).first()


def save_group(session: Session, group: Group) -> Group:
    group.updated_at = _now()
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def delete_group(session: Session, group_id: int) -> Group | None:
    group = session.get(Group, group_id)
    if group is None:
        return None
    if group.deleted_at is None:
        group.deleted_at = _now()
    group.enabled = False
    group.wechat_send_enabled = False
    group.updated_at = _now()
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def restore_group(session: Session, group_id: int) -> Group | None:
    group = session.get(Group, group_id)
    if group is None:
        return None
    if group.deleted_at is None:
        return group
    group.deleted_at = None
    group.enabled = False
    group.wechat_send_enabled = False
    group.updated_at = _now()
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


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
