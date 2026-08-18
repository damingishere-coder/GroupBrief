"""GroupBrief V2 P6 手动测试入口：微信自动发送（dry_run + 真实）。

安全约束：默认发送目标为「文件传输助手」，禁止直接批量发送正式群。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts/test_wechat_send.py health
    .venv\\Scripts\\python.exe scripts/test_wechat_send.py dry-run --target "文件传输助手"
    .venv\\Scripts\\python.exe scripts/test_wechat_send.py send --target "文件传输助手" --text "测试消息"
    .venv\\Scripts\\python.exe scripts/test_wechat_send.py send-image --target "文件传输助手" --image <path>

真实发送要求：微信已登录、电脑不锁屏、wechat-automation-api 配置正确。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.db import repository as repo
from app.sender.wechat_automation import WechatAutomationSender

DEFAULT_TARGET = "文件传输助手"


def _sender(dry_run: bool) -> WechatAutomationSender:
    settings = get_settings()
    repo.init_db(settings)
    repo.apply_db_settings(settings)
    return WechatAutomationSender(settings=settings, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief V2 微信发送测试")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("health", help="健康检查")
    p_dry = sub.add_parser("dry-run", help="dry_run 校验（不真正发送）")
    p_dry.add_argument("--target", default=DEFAULT_TARGET)
    p_send = sub.add_parser("send", help="发送文字")
    p_send.add_argument("--target", default=DEFAULT_TARGET)
    p_send.add_argument("--text", required=True)
    p_img = sub.add_parser("send-image", help="发送图片")
    p_img.add_argument("--target", default=DEFAULT_TARGET)
    p_img.add_argument("--image", required=True)
    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "health":
        sender = _sender(dry_run=False)
        ok, detail = sender.health_check()
        print(("✅ " if ok else "❌ ") + detail)
        return 0 if ok else 1

    if args.cmd == "dry-run":
        sender = _sender(dry_run=True)
        r1 = sender.send_text(args.target, "测试文字")
        r2 = sender.send_image(args.target, Path("output/test-data/ranking_茶馆_2026-08-17.txt"))
        print(r1.detail)
        print(r2.detail)
        return 0

    if args.cmd == "send":
        sender = _sender(dry_run=False)
        r = sender.send_text(args.target, args.text)
        print(("✅ " if r.success else "❌ ") + f"发送文字到 {args.target}：{r.detail}")
        return 0 if r.success else 1

    if args.cmd == "send-image":
        sender = _sender(dry_run=False)
        r = sender.send_image(args.target, args.image)
        print(("✅ " if r.success else "❌ ") + f"发送图片到 {args.target}：{r.detail}")
        return 0 if r.success else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
