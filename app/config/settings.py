"""GroupBrief 全局配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENVIRONMENT_ONLY_FIELDS = frozenset(
    {
        "allow_test_providers",
        "legacy_v1_write_mode",
        "scheduler_owner",
        "schedule_send_time",
        "reliability_watchdog_enabled",
        "reliability_lookback_days",
        "reliability_watchdog_interval_minutes",
        "wechat_fetch_max_attempts",
        "wechat_fetch_retry_backoff_seconds",
        "wechat_fetch_circuit_failure_threshold",
        "wechat_fetch_circuit_cooldown_seconds",
        "wechat_runtime_export_fallback_enabled",
        "image_prompt_max_chars",
        "image_prompt_max_bytes",
        "sqlite_busy_timeout_seconds",
        "sqlite_retry_max_attempts",
        "scheduler_heartbeat_stale_seconds",
        "weekly_insights_enabled",
        "weekly_send_enabled",
        "weekly_generate_time",
        "weekly_send_time",
        "output_root_override",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务（默认仅监听本机 8766 端口）
    app_host: str = "127.0.0.1"
    app_port: int = 8766
    app_timezone: str = "Asia/Shanghai"
    # 测试 Provider 安全闸门：真实运行默认关闭，且不通过设置 API/数据库修改。
    allow_test_providers: bool = False
    # 旧 V1 数据库流水线默认只读；短期兼容只能通过环境显式进入 maintenance。
    # 该字段不得加入设置 API/数据库，避免运行时误开双写。
    legacy_v1_write_mode: Literal["read_only", "maintenance"] = "read_only"

    # 数据库
    database_url: str = "sqlite:///data/groupbrief.db"
    sqlite_busy_timeout_seconds: int = 15
    sqlite_retry_max_attempts: int = 3

    # V1 兼容历史读取；正式 V2 使用下方 WeChatDataAnalysis MCP/导出配置。
    history_provider_primary: str = "wechat_data_analysis"
    history_provider_fallback: str = "wechat_cli"
    history_provider_mock_enabled: bool = False
    wechat_data_dir: str = ""
    wechat_export_dir: str = ""
    wechat_cli_path: str = ""

    # WeChatDataAnalysis 本地 MCP 服务（可选，启用后优先于 JSON 导出）
    # 仅允许本机回环地址（127.0.0.1 / localhost / ::1）；token 为敏感值，
    # 不会通过设置 API 回显。未配置时回退到结构化 JSON 导出（wechat_export_dir）。
    wechat_mcp_url: str = "http://127.0.0.1:10392/mcp"
    wechat_mcp_token: str = ""
    wechat_mcp_account: str = ""
    wechat_mcp_timeout_seconds: int = 10
    # 单页范围读取允许更长超时；整组读取总时限独立控制，兼容旧分页路径。
    wechat_mcp_range_timeout_seconds: int = 60
    wechat_fetch_total_timeout_seconds: int = 600
    wechat_fetch_max_attempts: int = 3
    wechat_fetch_retry_backoff_seconds: float = 1.0
    wechat_fetch_circuit_failure_threshold: int = 3
    wechat_fetch_circuit_cooldown_seconds: int = 300
    # 仅当 MCP 明确读取失败且完整 JSON 导出能独立返回同一时间窗时切换；不拼接两源。
    wechat_runtime_export_fallback_enabled: bool = True
    # 额外允许的 MCP 主机（逗号分隔，仅 Docker 容器访问宿主机场景使用，
    # 如 host.docker.internal）。默认空：仍只允许本机回环地址。
    wechat_mcp_allowed_hosts: str = ""
    # 微信联系人数据库（contact.db）路径，用于把微信号解析成真实显示名。
    # 留空时自动探测 WeChatDataAnalysis 的解密数据库。
    wechat_contact_db_path: str = ""

    # 群聊总结主备路由：Codex GPT 主用，DeepSeek 备用。
    summary_provider_primary: str = "codex"
    summary_provider_fallback: str = "deepseek"
    codex_summary_model: str = "gpt-5.6-sol"
    # 结构化群聊整理在高峰期可能超过 4 分钟；600 秒仍有明确上限，
    # 同时避免把正常的长响应误判成不可自动恢复的结果未知。
    codex_summary_timeout_seconds: int = 600
    codex_summary_max_retries: int = 2
    codex_summary_request_concurrency: int = 2

    # DeepSeek 备用
    # 旧设置兼容字段；真实路由只使用 summary_provider_primary/fallback。
    # 不再通过设置 API 暴露，保留一版以兼容旧 .env/数据库。
    ai_provider: str = "deepseek"
    ai_base_url: str = "https://api.deepseek.com"
    ai_api_key: str = ""
    ai_model: str = "deepseek-v4-flash"
    ai_timeout_seconds: int = 60
    ai_max_retries: int = 3
    # 兼容旧配置：不再参与实际分段，保留一版以避免旧 .env 启动失败。
    chunk_message_count: int = 60
    # 典型群在此字符预算内整群一次提交；超长群才进入自然会话分段。
    max_context_chars: int = 50000
    generation_group_concurrency: int = 5
    # WeChatDataAnalysis 旧分页接口按单请求串行最稳定；群生成仍可并发等待取数槽位。
    wechat_fetch_concurrency: int = 1
    ai_request_concurrency: int = 6

    # Codex $imagegen（V2 图片生成）
    # codex 可执行文件路径（留空则尝试 PATH 中的 codex）
    codex_path: str = ""
    # Codex 用户目录；认证与 generated_images 必须来自同一目录。
    codex_home: str = ""
    codex_timeout_seconds: int = 1200
    codex_generated_images_dir: str = ""  # 留空时默认 ~/.codex/generated_images
    # Codex/ImageGen 任务在可靠结构化回执下允许受控并发；默认两路。
    image_generation_concurrency: int = 2
    # 本地 Level 3 信息图字体；留空时按 Windows 常见中文字体顺序探测。
    image_fallback_font_path: str = ""
    # 最终 image_prompt.txt 的硬边界；仅部署环境可调，避免运行时误设为无限。
    image_prompt_max_chars: int = 24000
    image_prompt_max_bytes: int = 65536

    # 微信发送（V2）。默认适配微信 4.1.x 的 Windows 键盘/剪贴板/OCR 驱动；
    # legacy_cli 保留旧 wechat-automation-api 兼容入口。
    wechat_sender_mode: str = "native"
    wechat_native_action_delay_seconds: float = 0.6
    wechat_native_stage_timeout_seconds: float = 5.0
    wechat_native_submit_timeout_seconds: float = 8.0
    wechat_native_poll_interval_seconds: float = 0.2
    wechat_native_mutex_timeout_seconds: float = 20.0
    wechat_send_claim_seconds: int = 180
    wechat_late_send_window_minutes: int = 30
    # 旧 CLI 兼容配置
    # scripts/skill_cli.py 的绝对路径；留空则 WechatAutomationSender 不可用
    wechat_automation_cli_path: str = ""
    # 运行 wechat-automation-api 的 Python（其独立 venv）
    wechat_automation_python: str = ""
    # 预留：微信窗口 UIA 类名适配（当前微信 4.1.12 自绘 UI 不兼容，留空自动探测）
    wechat_window_class: str = ""

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
    # fastapi：8766 内 APScheduler 是唯一 owner；external：仅允许外部调度；
    # disabled：不注册任何自动任务。该字段只由环境配置，不通过设置 API 修改。
    scheduler_owner: Literal["fastapi", "external", "disabled"] = "fastapi"
    schedule_generate_time: str = "00:15"
    # 日报微信发送采用唯一全局批次时间；群级 send_time 仅保留数据库兼容。
    schedule_send_time: str = "08:30"
    schedule_email_time: str = "after_generate"
    schedule_startup_catchup_enabled: bool = True
    # 无人值守恢复只在进程启动时检查一次，不再注册固定频率 Watchdog。
    # 字段名保留一版以兼容既有部署环境。
    reliability_watchdog_enabled: bool = True
    # 自动恢复严格限制为当前日和前一日（48 小时产品边界）。更早任务只预览。
    reliability_lookback_days: int = 2
    reliability_watchdog_interval_minutes: int = 10  # 已弃用，仅兼容旧配置
    # 周报能力先部署、后灰度：14 天可靠性验收完成前保持关闭。
    weekly_insights_enabled: bool = False
    weekly_send_enabled: bool = False
    weekly_generate_time: str = "07:45"
    weekly_send_time: str = "08:30"
    scheduler_heartbeat_stale_seconds: int = 300

    # 只供测试/离线执行通过环境变量隔离 output 与相邻 runtime；
    # 生产默认留空，路径合同保持不变，且设置 API 无权修改。
    output_root_override: str = ""

    # 路径
    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def output_dir(self) -> Path:
        if self.output_root_override:
            return Path(self.output_root_override).expanduser().resolve()
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
            if key not in field_map or key in _ENVIRONMENT_ONLY_FIELDS:
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
        raise ValueError(f"{key} 必须是 true/false")
    if annotation is int or annotation == int:
        if isinstance(raw, bool):
            return int(raw)
        return int(str(raw).strip())
    if annotation is float or annotation == float:
        if isinstance(raw, bool):
            return float(int(raw))
        return float(str(raw).strip())
    return str(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
