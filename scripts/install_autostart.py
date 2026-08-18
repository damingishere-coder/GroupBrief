"""GroupBrief V2 开机自启安装/卸载（P9）。

通过 Windows 注册表 Run 键（HKCU）在用户登录时自动启动 GroupBrief 服务。
不绕过锁屏安全机制：服务仅在用户登录后运行（锁屏状态下不发送微信，
由系统状态页明确提示）。

用法：
    .venv\\Scripts\\python.exe scripts/install_autostart.py install
    .venv\\Scripts\\python.exe scripts/install_autostart.py uninstall
    .venv\\Scripts\\python.exe scripts/install_autostart.py status
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "GroupBriefV2"

# 登录时以最小化窗口启动 V1 启动脚本（该脚本会启动后端服务并打开浏览器）
_START_CMD = f'cmd /c start "" /min "{PROJECT_ROOT / "start_windows.bat"}"'


def _install() -> str:
    try:
        import winreg
    except ImportError:
        return "当前环境无 winreg（非 Windows），无法安装"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _START_CMD)
    return f"已安装开机自启：{_START_CMD}"


def _uninstall() -> str:
    try:
        import winreg
    except ImportError:
        return "当前环境无 winreg（非 Windows），无法卸载"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return "已移除开机自启"
    except FileNotFoundError:
        return "未安装开机自启"


def _status() -> str:
    try:
        import winreg
    except ImportError:
        return "非 Windows 环境"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
        return f"已安装：{value}"
    except FileNotFoundError:
        return "未安装"


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief 开机自启管理")
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    args = parser.parse_args()
    if args.action == "install":
        print(_install())
    elif args.action == "uninstall":
        print(_uninstall())
    else:
        print(_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
