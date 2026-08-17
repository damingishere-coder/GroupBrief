"""GroupBrief SQLite 数据模型。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: int | None = Field(default=None, primary_key=True)
    display_name: str = Field(default="", max_length=128)
    wechat_group_id: str = Field(default="", max_length=128, index=True)
    wechat_group_name: str = Field(default="", max_length=256)
    enabled: bool = True
    provider_preference: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    report_date: str = ""  # YYYY-MM-DD（报告归属日）
    range_start: str = ""  # ISO 时间
    range_end: str = ""
    trigger_type: str = "manual"  # auto / manual
    status: str = "running"  # running / success / partial / failed
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = None
    error_message: str = ""


class GroupRun(SQLModel, table=True):
    __tablename__ = "group_runs"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    group_id: int = Field(index=True)
    provider_used: str = ""
    message_count: int = 0
    speaker_count: int = 0
    ranking_status: str = "pending"  # pending / success / failed / skipped
    prompt_status: str = "pending"  # pending / success / failed / skipped
    error_message: str = ""


class Report(SQLModel, table=True):
    __tablename__ = "reports"

    id: int | None = Field(default=None, primary_key=True)
    group_run_id: int = Field(index=True)
    ranking_text: str = ""
    prompt_text: str = ""
    ranking_file: str = ""
    prompt_file: str = ""
    poster_file: str = ""  # V2 使用，V1 为空
    poster_status: str = ""  # V2 使用
    email_status: str = ""  # none / sent / failed / skipped
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True, max_length=128)
    value: str = ""
    updated_at: datetime = Field(default_factory=datetime.now)


class ProviderHealth(SQLModel, table=True):
    __tablename__ = "provider_health"

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    status: str = ""  # OK / UNAVAILABLE / UNSUPPORTED_WECHAT_VERSION / ...
    detail: str = ""
    checked_at: datetime = Field(default_factory=datetime.now)


class ExecutionLog(SQLModel, table=True):
    __tablename__ = "execution_logs"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = None
    level: str = "info"  # info / warning / error
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
