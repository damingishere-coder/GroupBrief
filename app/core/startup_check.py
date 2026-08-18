"""P9 启动检查：服务启动时验证关键外部依赖与本地环境。

检查项：WeChatDataAnalysis 数据源 / 微信进程 / DeepSeek 配置 /
output 可写 / templates 完整。任一失败只记录日志并提示，不阻止启动
（避免单点失败导致整个服务退出）。

外部调用均可在测试中注入替身（monkeypatch WeChatDataAnalysisSource /
_run_tasklist），保证本地检查不触网。
"""

from __future__ import annotations

import subprocess

from app.config.settings import Settings
from app.core.logging import get_logger
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource

logger = get_logger("app")


def _tasklist_wechat() -> bool:
    """微信进程是否在运行（本地命令）。"""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return "Weixin.exe" in out.stdout


def run_startup_checks(settings: Settings) -> list[dict]:
    """执行全部启动检查，返回 [{name, ok, status, detail}]。"""
    checks: list[dict] = []

    # 1) WeChatDataAnalysis 数据源
    try:
        h = WeChatDataAnalysisSource(settings=settings).health_check()
        checks.append(
            {
                "name": "WeChatDataAnalysis 数据源",
                "ok": h.ok,
                "status": h.status.value,
                "detail": h.detail,
            }
        )
    except Exception as e:
        checks.append({"name": "WeChatDataAnalysis 数据源", "ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]})

    # 2) 微信进程
    try:
        running = _tasklist_wechat()
        checks.append(
            {
                "name": "微信客户端",
                "ok": running,
                "status": "OK" if running else "OFFLINE",
                "detail": "微信已登录" if running else "微信 PC 客户端未运行（需登录且不锁屏）",
            }
        )
    except Exception as e:
        checks.append({"name": "微信客户端", "ok": False, "status": "OFFLINE", "detail": str(e)[:200]})

    # 3) DeepSeek 配置
    key_ok = bool(settings.ai_api_key)
    checks.append(
        {
            "name": "DeepSeek V4 Flash",
            "ok": key_ok,
            "status": "OK" if key_ok else "UNAVAILABLE",
            "detail": f"模型 {settings.ai_model}；API Key {'已配置' if key_ok else '未配置（将使用本地模板）'}",
        }
    )

    # 4) output 可写
    try:
        settings.ensure_dirs()
        test = settings.output_dir / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        checks.append({"name": "输出目录", "ok": True, "status": "OK", "detail": f"{settings.output_dir} 可写"})
    except Exception as e:
        checks.append({"name": "输出目录", "ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]})

    # 5) templates 完整
    try:
        from app.ai.prompt_templates import ImagePromptTemplateService
        from app.ranking.template_service import RankingTemplateService

        ranking_names = RankingTemplateService().list_templates()
        prompt_names = ImagePromptTemplateService().list_templates()
        checks.append(
            {
                "name": "模板资产",
                "ok": "default" in ranking_names and "default" in prompt_names,
                "status": "OK",
                "detail": f"排行模板 {ranking_names} · Prompt 模板 {prompt_names}",
            }
        )
    except Exception as e:
        checks.append({"name": "模板资产", "ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]})

    for c in checks:
        level = logger.info if c["ok"] else logger.warning
        level("启动检查 [%s] %s：%s", c["status"], c["name"], c["detail"])
    return checks
