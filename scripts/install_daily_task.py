"""GroupBrief V2 两阶段计划任务管理（Windows 任务计划程序）。

- 每天 00:15 运行生成任务：统计前一天，生成排行榜、AI Prompt 和图片；
- 每天 08:30 起每分钟扫描一次，共 30 分钟：按群顺序发送排行榜和图片。

用法（项目根目录打开终端）：
    .venv\\Scripts\\python.exe scripts/install_daily_task.py install   # 安装
    .venv\\Scripts\\python.exe scripts/install_daily_task.py uninstall # 卸载
    .venv\\Scripts\\python.exe scripts/install_daily_task.py status    # 查看状态
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台 GBK 不支持 emoji，强制 UTF-8
GENERATE_TASK_NAME = "GroupBriefDaily"
SEND_TASK_NAME = "GroupBriefDailySend"
GENERATE_START_TIME = "00:15"
SEND_START_TIME = "08:30"
SEND_REPEAT_DURATION = "00:30"
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
DAILY_SCRIPT = PROJECT_ROOT / "scripts" / "daily_auto.py"
PIPELINE_SCRIPT = PROJECT_ROOT / "scripts" / "run_daily_pipeline.py"

# 兼容旧脚本导入方。
TASK_NAME = GENERATE_TASK_NAME
START_TIME = GENERATE_START_TIME

# 任务命令行：schtasks /TR 需要把整个命令用引号包住，内部的路径也各自加引号
_GENERATE_CMD = f'"{PYTHON_EXE}" "{DAILY_SCRIPT}"'
_SEND_CMD = f'"{PYTHON_EXE}" "{PIPELINE_SCRIPT}" send'


def _run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _install() -> str:
    if not PYTHON_EXE.exists():
        return f"❌ 未找到虚拟环境 Python：{PYTHON_EXE}，请先在项目根目录创建 .venv"

    generate_code, generate_out = _run([
        "schtasks", "/Create", "/F",
        "/TN", GENERATE_TASK_NAME,
        "/TR", _GENERATE_CMD,
        "/SC", "DAILY",
        "/ST", GENERATE_START_TIME,
    ])
    if generate_code != 0:
        return f"❌ 生成任务安装失败：\n{generate_out.strip()}"

    send_code, send_out = _run([
        "schtasks", "/Create", "/F",
        "/TN", SEND_TASK_NAME,
        "/TR", _SEND_CMD,
        "/SC", "DAILY",
        "/ST", SEND_START_TIME,
        "/RI", "1",
        "/DU", SEND_REPEAT_DURATION,
    ])
    if send_code != 0:
        return (
            f"⚠️ 已安装 {GENERATE_TASK_NAME}，但发送任务安装失败：\n"
            f"{send_out.strip()}"
        )
    return (
        f"✅ 已安装两阶段计划任务：每天 {GENERATE_START_TIME} 生成前一日群报；"
        f"{SEND_START_TIME} 起 30 分钟内每分钟扫描并顺序发送\n"
        f"{generate_out.strip()}\n{send_out.strip()}"
    )


def _uninstall() -> str:
    messages: list[str] = []
    removed = 0
    for task_name in (GENERATE_TASK_NAME, SEND_TASK_NAME):
        code, out = _run(["schtasks", "/Delete", "/TN", task_name, "/F"])
        if code == 0:
            removed += 1
            messages.append(f"✅ 已卸载计划任务「{task_name}」")
        else:
            messages.append(f"ℹ️ 计划任务「{task_name}」未安装：{out.strip() or '无记录'}")
    if removed == 0:
        messages.insert(0, "ℹ️ 没有已安装的 GroupBrief 两阶段计划任务")
    return "\n".join(messages)


def _status_one(task_name: str) -> str:
    code, out = _run(["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"])
    if code != 0:
        return "❌ 计划任务「%s」未安装（%s）" % (task_name, out.strip() or "无记录")
    # 提取关键字段，避免整页输出
    lines = [f"【{task_name}】"]
    for key in ("任务名", "下次运行时间", "上次运行时间", "上次结果", "状态", "要运行的任务"):
        for line in out.splitlines():
            if line.strip().startswith(key):
                lines.append(line.strip())
                break
    return "\n".join(lines) if len(lines) > 1 else f"【{task_name}】\n{out.strip()}"


def _status() -> str:
    return "\n\n".join(
        _status_one(task_name) for task_name in (GENERATE_TASK_NAME, SEND_TASK_NAME)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief 每日计划任务管理")
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
