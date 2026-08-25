r"""GroupBrief V2 外部调度回滚入口（Windows 任务计划程序）。

正式默认 owner 是 8766 内 APScheduler。Windows 两个旧任务只能在明确配置
``SCHEDULER_OWNER=external`` 后安装或启用，禁止与 FastAPI owner 同时工作。

用法（项目根目录打开终端）：
    .venv\Scripts\python.exe scripts/install_daily_task.py status
    .venv\Scripts\python.exe scripts/install_daily_task.py disable
    .venv\Scripts\python.exe scripts/install_daily_task.py enable     # 仅 external owner
    .venv\Scripts\python.exe scripts/install_daily_task.py install    # 仅 external owner
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.scheduler.outcome import ProcessExitCode

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

_GENERATE_CMD = f'"{PYTHON_EXE}" "{DAILY_SCRIPT}"'
_SEND_CMD = f'"{PYTHON_EXE}" "{PIPELINE_SCRIPT}" send'


def _run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _configured_owner(owner: str | None = None) -> str:
    return owner or get_settings().scheduler_owner


def _install(owner: str | None = None) -> tuple[int, str]:
    if _configured_owner(owner) != "external":
        return (
            int(ProcessExitCode.BLOCKED),
            "❌ 当前 scheduler_owner 不是 external，拒绝创建 Windows 业务任务",
        )
    if not PYTHON_EXE.exists():
        return (
            int(ProcessExitCode.FAILED),
            f"❌ 未找到虚拟环境 Python：{PYTHON_EXE}，请先创建 .venv",
        )

    generate_code, generate_out = _run(
        [
            "schtasks", "/Create", "/F", "/TN", GENERATE_TASK_NAME,
            "/TR", _GENERATE_CMD, "/SC", "DAILY", "/ST", GENERATE_START_TIME,
        ]
    )
    if generate_code != 0:
        return int(ProcessExitCode.FAILED), f"❌ 生成任务安装失败：\n{generate_out.strip()}"

    send_code, send_out = _run(
        [
            "schtasks", "/Create", "/F", "/TN", SEND_TASK_NAME,
            "/TR", _SEND_CMD, "/SC", "DAILY", "/ST", SEND_START_TIME,
            "/RI", "1", "/DU", SEND_REPEAT_DURATION,
        ]
    )
    if send_code != 0:
        rollback_code, rollback_out = _run(
            ["schtasks", "/Delete", "/TN", GENERATE_TASK_NAME, "/F"]
        )
        rollback_detail = (
            "已回滚生成任务"
            if rollback_code == 0
            else f"生成任务回滚失败：{rollback_out.strip()}"
        )
        code = ProcessExitCode.FAILED if rollback_code == 0 else ProcessExitCode.PARTIAL
        return int(code), f"❌ 发送任务安装失败；{rollback_detail}：\n{send_out.strip()}"

    return (
        int(ProcessExitCode.SUCCESS),
        f"✅ 已安装 external owner 两阶段任务：每天 {GENERATE_START_TIME} 生成；"
        f"{SEND_START_TIME} 起 30 分钟内每分钟扫描\n"
        f"{generate_out.strip()}\n{send_out.strip()}",
    )


def _uninstall() -> tuple[int, str]:
    messages: list[str] = []
    removed = 0
    failures = 0
    for task_name in (GENERATE_TASK_NAME, SEND_TASK_NAME):
        code, out = _run(["schtasks", "/Delete", "/TN", task_name, "/F"])
        if code == 0:
            removed += 1
            messages.append(f"✅ 已卸载计划任务「{task_name}」")
        else:
            failures += 1
            messages.append(f"ℹ️ 计划任务「{task_name}」未卸载：{out.strip() or '无记录'}")
    if removed == 0:
        messages.insert(0, "ℹ️ 没有已安装的 GroupBrief 两阶段任务")
    code = ProcessExitCode.PARTIAL if removed and failures else ProcessExitCode.SUCCESS
    return int(code), "\n".join(messages)


def _query_enabled(task_name: str) -> tuple[bool, bool | None, str]:
    code, out = _run(["schtasks", "/Query", "/TN", task_name, "/XML"])
    if code != 0:
        return False, None, out.strip() or "未安装"
    xml_text = out.lstrip("\ufeff\r\n ")
    if xml_text.startswith("<?xml") and "?>" in xml_text:
        xml_text = xml_text.split("?>", 1)[1]
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return True, None, "任务 XML 无法解析"
    enabled_text = next(
        (node.text for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "Enabled"),
        None,
    )
    if enabled_text is None:
        # Task Scheduler Schema 中 Enabled 可省略，省略时默认 true。
        return True, True, ""
    return True, enabled_text.strip().lower() == "true", ""


def _status(owner: str | None = None) -> tuple[int, str]:
    owner = _configured_owner(owner)
    states = {name: _query_enabled(name) for name in (GENERATE_TASK_NAME, SEND_TASK_NAME)}
    lines = [f"scheduler_owner={owner}"]
    for name, (exists, enabled, detail) in states.items():
        if not exists:
            state = "not_installed"
        elif enabled is True:
            state = "enabled"
        elif enabled is False:
            state = "disabled"
        else:
            state = "unknown"
        lines.append(f"{name}={state}{f' ({detail})' if detail else ''}")

    enabled_count = sum(enabled is True for _, enabled, _ in states.values())
    installed_count = sum(exists for exists, _, _ in states.values())
    unknown_count = sum(exists and enabled is None for exists, enabled, _ in states.values())
    conflict = (
        unknown_count > 0
        or (owner in {"fastapi", "disabled"} and enabled_count > 0)
        or (owner == "external" and (installed_count != 2 or enabled_count != 2))
    )
    if conflict:
        lines.append("outcome=blocked：配置 owner 与 Windows 任务状态不一致")
        return int(ProcessExitCode.BLOCKED), "\n".join(lines)
    lines.append("outcome=success")
    return int(ProcessExitCode.SUCCESS), "\n".join(lines)


def _set_enabled(enabled: bool, owner: str | None = None) -> tuple[int, str]:
    if enabled and _configured_owner(owner) != "external":
        return int(ProcessExitCode.BLOCKED), "❌ 只有 external owner 才能启用 Windows 业务任务"
    messages: list[str] = []
    failures = 0
    for task_name in (GENERATE_TASK_NAME, SEND_TASK_NAME):
        exists, current, detail = _query_enabled(task_name)
        if not exists:
            messages.append(f"ℹ️ {task_name} 未安装")
            continue
        if current is enabled:
            messages.append(f"✅ {task_name} 已是 {'enabled' if enabled else 'disabled'}")
            continue
        code, out = _run(
            ["schtasks", "/Change", "/TN", task_name, "/Enable" if enabled else "/Disable"]
        )
        if code == 0:
            messages.append(f"✅ {task_name} 已{'启用' if enabled else '禁用'}")
        else:
            failures += 1
            messages.append(f"❌ {task_name} 切换失败：{out.strip() or detail}")
    return (
        int(ProcessExitCode.FAILED if failures else ProcessExitCode.SUCCESS),
        "\n".join(messages),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="GroupBrief Windows 外部调度回滚入口")
    parser.add_argument("action", choices=["install", "uninstall", "status", "disable", "enable"])
    args = parser.parse_args()
    if args.action == "install":
        code, message = _install()
    elif args.action == "uninstall":
        code, message = _uninstall()
    elif args.action == "disable":
        code, message = _set_enabled(False)
    elif args.action == "enable":
        code, message = _set_enabled(True)
    else:
        code, message = _status()
    print(message)
    print(f"OUTCOME exit_code={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
