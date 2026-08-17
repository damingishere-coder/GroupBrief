"""GroupBrief 全局配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

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
    wechat_export_dir: str = ""
    wechat_cli_path: str = ""

    # WeChatDataAnalysis 本地 MCP 服务（可选，启用后优先于 JSON 导出）
    # 仅允许本机回环地址（127.0.0.1 / localhost / ::1）；token 为敏感值，
    # 不会通过设置 API 回显。未配置时回退到结构化 JSON 导出（wechat_export_dir）。
    wechat_mcp_url: str = "http://127.0.0.1:10392/mcp"
    wechat_mcp_token: str = ""
    wechat_mcp_account: str = ""
    wechat_mcp_timeout_seconds: int = 60

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

    def apply_runtime_values(self, values: dict[str, Any]) -> list[str]:
        """把持久化设置安全应用到当前运行实例。

        - 仅应用 Settings 已知字段；
        - 保持字段原始类型（bool/int/string）；
        - 忽略掩码敏感值（"******"），绝不覆盖运行时非空值；
        - 不会把凭据写入数据库（调用方负责）。
        返回实际生效的字段名列表。
        """
        applied: list[str] = []
        field_map = Settings.model_fields
        for key, raw in values.items():
            if key not in field_map:
                continue
            if isinstance(raw, str) and raw.strip() == "******":
                continue
            try:
                converted = _coerce_setting_value(key, raw, field_map[key].annotation)
            except (TypeError, ValueError):
                continue
            setattr(self, key, converted)
            applied.append(key)
        return applied


_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off", ""})


def _coerce_setting_value(key: str, raw: Any, annotation: Any) -> Any:
    """把持久化的字符串设置转换为字段声明的类型。"""
    if annotation is bool or annotation == bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
        return bool(text)
    if annotation is int or annotation == int:
        if isinstance(raw, bool):
            return int(raw)
        return int(str(raw).strip())
    return str(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
