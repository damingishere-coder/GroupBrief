"""GroupBrief 全局配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务（8765 被本机其他项目占用，默认使用 8766）
    app_host: str = "127.0.0.1"
    app_port: int = 8766
    app_timezone: str = "Asia/Shanghai"

    # 数据库
    database_url: str = "sqlite:///data/groupbrief.db"

    # 微信历史读取
    history_provider_primary: str = "wechat_data_analysis"
    history_provider_fallback: str = "wechat_cli"
    history_provider_mock_enabled: bool = True
    wechat_data_dir: str = ""
    wechat_cli_path: str = ""

    # DeepSeek
    ai_provider: str = "deepseek"
    ai_base_url: str = "https://api.deepseek.com"
    ai_api_key: str = ""
    ai_model: str = "deepseek-chat"
    ai_timeout_seconds: int = 60
    ai_max_retries: int = 3
    chunk_message_count: int = 60
    max_context_chars: int = 12000

    # 邮件
    email_enabled: bool = False
    email_recipient: str = ""
    email_from: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 465
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_use_ssl: bool = True
    email_send_partial_report: bool = True

    # 自动任务
    schedule_generate_time: str = "08:45"
    schedule_email_time: str = "09:00"

    # 路径
    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def output_dir(self) -> Path:
        return PROJECT_ROOT / "output"

    @property
    def logs_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def fixtures_dir(self) -> Path:
        return PROJECT_ROOT / "fixtures"

    @property
    def db_path(self) -> Path:
        url = self.database_url
        if url.startswith("sqlite:///"):
            rel = url[len("sqlite:///"):]
            return PROJECT_ROOT / rel
        raise ValueError("仅支持 sqlite 数据库")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.output_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
