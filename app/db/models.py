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
    deleted_at: datetime | None = Field(default=None, index=True)

    # ---------- V2 扩展字段（P3 起使用，P7 pipeline 落地） ----------
    schedule_rule: str = "weekday_default"  # 统计周期规则
    send_time: str = "08:30"  # 本群独立发送时间 HH:MM
    summary_model: str = "gpt-5.6-sol"  # 总结主模型
    prompt_model: str = "gpt-5.6-sol"  # Prompt 主模型
    image_enabled: bool = True  # 是否生图
    send_target: str = ""  # 可选人工发送目标；为空时自动跟随 wechat_group_name
    ranking_template: str = "default"  # 排行榜模板名
    image_prompt_template: str = "default"  # 生图 Prompt 模板名
    image_theme: str = "random_preset"  # 生图大主题键（默认每日随机）
    image_theme_custom: str = ""  # 自定义生图大主题（image_theme=custom 时使用）
    image_prompt_override: str = ""  # 本群专属 Prompt 模板；为空时继承全局模板
    wechat_send_enabled: bool = False  # 独立于生成开关，默认禁止自动对外发送


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
