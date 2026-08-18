"""V2 WechatAutomationSender（P6）。

通过 wechat-automation-api 的 CLI 入口（scripts/skill_cli.py，基于 Windows UI
Automation 控制微信）发送文字与本地图片。GroupBrief 不引入第三方代码，
仅以子进程调用其 CLI，便于更换 Provider。

当前已知限制（2026-08-18 实测）：
- 微信 4.1.12.55 为自绘 UI，标准 UIA 控件树不可用（只有 2 个 Pane），
  wechat-automation-api 依赖的 mmui::ChatInputField 等控件定位不到；
- skill_cli.py 对当前微信返回 WECHAT_WINDOW_NOT_FOUND。
因此 health_check 会如实反映不可用；真实发送需微信版本或方案调整后验证。

dry_run=true 时只校验参数与目标，不真正调用外部进程。
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.sender.base import SendResult, WechatSender
from app.v2.constants import WECHAT_OFFLINE

logger = get_logger("groupbrief.sender")


class WechatAutomationSender(WechatSender):
    name = "wechat_automation"

    def __init__(
        self,
        settings: Settings | None = None,
        cli_path: str | None = None,
        python_path: str | None = None,
        dry_run: bool = False,
    ):
        self.settings = settings or get_settings()
        self.cli_path = cli_path or self.settings.wechat_automation_cli_path
        self.python_path = python_path or self.settings.wechat_automation_python or "python"
        self.dry_run = dry_run
        # 手动测试模式：微信窗口 UIA 适配（预留，当前微信版本不兼容）
        self.wechat_window_class = self.settings.wechat_window_class or ""

    # ---------- 健康检查 ----------

    def health_check(self) -> tuple[bool, str]:
        if not self.cli_path or not Path(self.cli_path).exists():
            return False, f"wechat-automation-api CLI 不存在：{self.cli_path or '(未配置)'}"
        if not self._wechat_running():
            return False, WECHAT_OFFLINE
        ok, detail = self._probe_cli()
        return ok, detail

    def _wechat_running(self) -> bool:
        try:
            import subprocess as sp

            out = sp.run(
                ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return "Weixin.exe" in out.stdout
        except Exception:
            return False

    def _probe_cli(self) -> tuple[bool, str]:
        """探测 CLI 是否可启动（执行 --help）。"""
        try:
            proc = subprocess.run(
                [self.python_path, self.cli_path, "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0 and "skill_cli" in proc.stdout:
                return True, f"CLI 可用：{self.cli_path}"
            return False, f"CLI 探测失败（exit={proc.returncode}）：{proc.stderr[-200:] or proc.stdout[-200:]}"
        except Exception as e:
            return False, f"CLI 探测异常：{e}"

    # ---------- 发送 ----------

    def send_text(self, target: str, text: str) -> SendResult:
        if self.dry_run:
            return SendResult(True, f"[dry_run] 发送文字到 {target}（{len(text)} 字符）", _now())
        return self._send(target, text, "sendtext")

    def send_image(self, target: str, image_path) -> SendResult:
        path = Path(image_path)
        if not path.exists():
            return SendResult(False, f"图片不存在：{path}", _now())
        if self.dry_run:
            return SendResult(True, f"[dry_run] 发送图片到 {target}：{path}", _now())
        return self._send(target, str(path.resolve()), "sendpic")

    def _send(self, target: str, content: str, action: str) -> SendResult:
        cmd = [
            self.python_path,
            self.cli_path,
            "--to",
            target,
            "--content",
            content,
            "--action",
            action,
            "--json",
            "--no-api",  # 不依赖常驻 HTTP 服务
        ]
        logger.info("调用 wechat-automation-api：%s", " ".join(cmd)[:160])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e:
            logger.exception("调用 wechat-automation-api 异常")
            return SendResult(False, f"调用异常：{e}", _now())

        output = (proc.stdout or "").strip() + (proc.stderr or "").strip()
        # --json 输出形如 {"success": true/false, "code": "...", "message": "..."}
        import json

        detail = output[-500:]
        try:
            parsed = json.loads(output[output.find("{"):])
            success = bool(parsed.get("success"))
            code = parsed.get("code") or ""
            message = parsed.get("message") or ""
            return SendResult(success, f"{code} {message}".strip() or detail, _now())
        except Exception:
            return SendResult(proc.returncode == 0, detail, _now())


def _now() -> str:
    return datetime.now().isoformat()
