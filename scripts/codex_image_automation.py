r"""供 Codex Desktop 自动化使用的 GroupBrief 图片交接脚本。

本脚本不启动 Codex CLI，也不直接调用图片 API。它只负责：

1. 列出已经生成 ``image_prompt.txt``、但还缺少有效日报图片的任务；
2. 在 Codex 调用内置 ImageGen 前记录 ``$CODEX_HOME/generated_images`` 快照；
3. 在 ImageGen 完成后认领本次新增 PNG，复制到
   ``output/<群>/<日期>/daily_image.png``；
4. 验证图片并把 ``run.json`` 更新为 ``READY_TO_SEND``。

典型用法（项目根目录）：

    .venv\Scripts\python.exe scripts\codex_image_automation.py pending  # 默认查询应用时区昨天的报告归属日
    # 以下命令使用真实存储日期 run_date=2026-08-21；本批任务的 report_date=2026-08-20
    .venv\Scripts\python.exe scripts\codex_image_automation.py begin --group "群名" --date 2026-08-21
    # 由 Codex 自动化使用内置 image_gen 工具生成图片
    .venv\Scripts\python.exe scripts\codex_image_automation.py adopt --group "群名" --date 2026-08-21
    .venv\Scripts\python.exe scripts\codex_image_automation.py verify --group "群名" --date 2026-08-21
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config.settings import get_settings
from app.image.image_task import copy_generated_image, detect_image_format, verify_image
from app.v2.constants import (
    FAILED,
    IMAGE_FILE_MISSING,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    PROMPT_READY,
    READY_TO_SEND,
    SENT,
)
from app.v2.run_store import RunStore, validate_run_date


MARKER_FILE = ".codex_image_automation.json"
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def _json_print(payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _image_enabled(run: dict[str, Any]) -> bool:
    value = run.get("image_enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _valid_date(value: Any) -> str | None:
    """返回有效的运行日期字符串；旧记录字段异常时安全返回 None。"""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        return validate_run_date(candidate)
    except ValueError:
        return None


def report_date_from_run(run: dict[str, Any]) -> str | None:
    """从运行记录推导报告归属日。

    新记录以 period_end 的自然日为准；兼容旧记录时依次回退到显式
    report_date 和 run_date，避免缺少 period_end 让 pending 整体失败。
    """
    period_end = run.get("period_end")
    if isinstance(period_end, str):
        report_date = _valid_date(period_end.strip()[:10])
        if report_date:
            return report_date
    elif isinstance(period_end, datetime):
        return period_end.date().isoformat()

    for field in ("report_date", "run_date"):
        report_date = _valid_date(run.get(field))
        if report_date:
            return report_date
    return None


def _effective_image_enabled(run: dict[str, Any]) -> bool:
    """优先读取数据库当前开关，数据库不可用时回退 run.json 快照。"""
    fallback = _image_enabled(run)
    group_id = run.get("group_id")
    group_name = str(run.get("group_name") or "").strip()
    try:
        settings = get_settings()
        db_path = settings.db_path
        if not db_path.is_file():
            return fallback

        # 使用 SQLite URI 的 mode=ro，自动化只读群配置，不创建或修改数据库。
        with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as connection:
            row = None
            if group_id not in (None, ""):
                try:
                    group_id_value = int(group_id)
                except (TypeError, ValueError):
                    group_id_value = None
                if group_id_value is not None:
                    row = connection.execute(
                        "SELECT image_enabled FROM groups WHERE id = ?",
                        (group_id_value,),
                    ).fetchone()

            if row is None and group_name:
                row = connection.execute(
                    "SELECT image_enabled FROM groups WHERE display_name = ? LIMIT 1",
                    (group_name,),
                ).fetchone()
            if row is None and group_name:
                row = connection.execute(
                    "SELECT image_enabled FROM groups WHERE wechat_group_name = ? LIMIT 1",
                    (group_name,),
                ).fetchone()
            if row is None:
                return fallback
            return _image_enabled({"image_enabled": row[0]})
    except Exception:
        # 数据库读取是增强信息；任何连接、表结构或配置异常都回退快照，
        # 不能让 pending/begin/adopt 因本地配置不可用而整体失败。
        return fallback


def _default_report_date(settings) -> str:
    """按应用时区计算昨天的报告归属日。"""
    try:
        timezone = ZoneInfo(settings.app_timezone)
    except (KeyError, TypeError, ValueError):
        # 配置异常时沿用本机时区，保证旧环境下 CLI 仍能给出可用查询日期。
        timezone = datetime.now().astimezone().tzinfo
    return (datetime.now(timezone).date() - timedelta(days=1)).isoformat()


def _eligible_status(run: dict[str, Any]) -> bool:
    if run.get("desktop_regen_requested") and run.get("image_regen_status") == "fallback_queued":
        return True
    status = str(run.get("status") or "")
    if status in {PROMPT_READY, IMAGE_READY, READY_TO_SEND}:
        return True
    if status != FAILED:
        return False
    return (
        run.get("failed_stage") == "image"
        or run.get("error_type") in {IMAGE_GENERATION_FAILED, IMAGE_FILE_MISSING}
        or bool(run.get("image_error"))
    )


def _existing_run(store: RunStore, group_name: str, run_date: str) -> dict[str, Any]:
    validate_run_date(run_date)
    run_path = store.run_path(group_name, run_date)
    if not run_path.is_file():
        raise ValueError(f"运行记录不存在：{group_name} / {run_date}")
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("run.json 无法读取或不是有效 JSON") from exc
    if not isinstance(run, dict):
        raise ValueError("run.json 内容格式错误")
    if str(run.get("group_name") or "") != group_name:
        raise ValueError("run.json 的群名与请求不一致")
    if str(run.get("run_date") or "") != run_date:
        raise ValueError("run.json 的日期与请求不一致")
    return run


def _task_from_run(store: RunStore, run: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    group_name = str(run.get("group_name") or "")
    run_date = str(run.get("run_date") or "")
    report_date = report_date_from_run(run)
    if (
        not group_name
        or not run_date
        or not report_date
        or not _effective_image_enabled(run)
        or not _eligible_status(run)
    ):
        return None
    try:
        validate_run_date(run_date)
    except ValueError:
        return None

    prompt_path = store.prompt_path(group_name, run_date)
    output_path = store.image_path(group_name, run_date)
    if not prompt_path.is_file():
        return None
    try:
        prompt_chars = len(prompt_path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return None
    if prompt_chars <= 0:
        return None
    regeneration = bool(run.get("desktop_regen_requested"))
    image_ok, _ = verify_image(output_path)
    if image_ok and not regeneration:
        return None
    return {
        "group_name": group_name,
        "run_date": run_date,
        "report_date": report_date,
        "status": str(run.get("status") or ""),
        "prompt_path": str(prompt_path.resolve()),
        "output_path": str(output_path.resolve()),
        "prompt_chars": prompt_chars,
        "regeneration": regeneration,
    }


def collect_pending(
    store: RunStore,
    run_date: str | None = None,
    limit: int = 5,
    report_date: str | None = None,
) -> list[dict[str, Any]]:
    if run_date is not None:
        validate_run_date(run_date)
    if report_date is not None:
        validate_run_date(report_date)
    tasks: list[dict[str, Any]] = []
    for run in store.list_runs(run_date):
        task = _task_from_run(store, run)
        if task and (
            task.get("regeneration")
            or report_date is None
            or task["report_date"] == report_date
        ):
            tasks.append(task)
    tasks.sort(key=lambda item: (item["run_date"], item["group_name"]))
    return tasks[:limit]


def _generated_images_dir() -> Path:
    settings = get_settings()
    configured = settings.codex_generated_images_dir.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return (Path(codex_home).expanduser() / "generated_images").resolve()
    return (Path.home() / ".codex" / "generated_images").resolve()


def _snapshot(directory: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    if not directory.is_dir():
        return result
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        result[str(path.resolve())] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    return result


def begin_task(store: RunStore, generated_dir: Path, group_name: str, run_date: str) -> dict[str, Any]:
    run = _existing_run(store, group_name, run_date)
    task = _task_from_run(store, run)
    if not task:
        raise ValueError("该任务不需要生图，或缺少有效 image_prompt.txt")
    marker_path = store.group_dir(group_name, run_date) / MARKER_FILE
    marker = {
        "group_name": group_name,
        "run_date": run_date,
        "started_at": datetime.now().astimezone().isoformat(),
        "generated_images_dir": str(generated_dir),
        "before": _snapshot(generated_dir),
        "regeneration": bool(task.get("regeneration")),
    }
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, **task, "marker_path": str(marker_path.resolve())}


def _load_marker(store: RunStore, group_name: str, run_date: str) -> tuple[Path, dict[str, Any]]:
    marker_path = store.group_dir(group_name, run_date) / MARKER_FILE
    if not marker_path.is_file():
        raise ValueError("未找到 begin 生成的快照标记，请先执行 begin")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("快照标记损坏，请重新执行 begin") from exc
    if marker.get("group_name") != group_name or marker.get("run_date") != run_date:
        raise ValueError("快照标记与当前任务不匹配")
    return marker_path, marker


def _marker_elapsed_ms(marker: dict[str, Any]) -> int:
    """计算 begin 到 adopt 的墙钟耗时，写入统一的生图计时字段。"""
    raw_started_at = str(marker.get("started_at") or "").strip()
    if not raw_started_at:
        return 0
    try:
        started_at = datetime.fromisoformat(raw_started_at)
    except ValueError:
        return 0
    if started_at.tzinfo is None:
        started_at = started_at.astimezone()
    return max(0, round((datetime.now().astimezone() - started_at).total_seconds() * 1000))


def _new_images(marker: dict[str, Any]) -> list[Path]:
    directory = Path(str(marker.get("generated_images_dir") or "")).resolve()
    before = marker.get("before") if isinstance(marker.get("before"), dict) else {}
    current = _snapshot(directory)
    candidates: list[Path] = []
    for raw_path, meta in current.items():
        old = before.get(raw_path)
        if not isinstance(old, dict) or old.get("mtime_ns") != meta.get("mtime_ns") or old.get("size") != meta.get("size"):
            path = Path(raw_path)
            if detect_image_format(path) == "png":
                candidates.append(path)
    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates


def _validate_source(source: Path, generated_dir: Path) -> Path:
    resolved = source.expanduser().resolve(strict=True)
    try:
        resolved.relative_to(generated_dir.resolve())
    except ValueError as exc:
        raise ValueError("只允许认领 $CODEX_HOME/generated_images 下的图片") from exc
    if detect_image_format(resolved) != "png":
        raise ValueError("ImageGen 输出不是 PNG，未写入日报图片")
    return resolved


def adopt_image(
    store: RunStore,
    group_name: str,
    run_date: str,
    source: Path | None = None,
) -> dict[str, Any]:
    run = _existing_run(store, group_name, run_date)
    if not _effective_image_enabled(run):
        raise ValueError("该群已关闭图片生成")
    marker_path, marker = _load_marker(store, group_name, run_date)
    generated_dir = Path(str(marker["generated_images_dir"])).resolve()
    selected = _validate_source(source, generated_dir) if source else None
    if selected is None:
        candidates = _new_images(marker)
        if not candidates:
            raise ValueError("ImageGen 执行后未发现本次新增 PNG；未修改 run.json")
        if len(candidates) > 1:
            raise ValueError(
                f"ImageGen 执行后发现 {len(candidates)} 个新增 PNG，无法安全判断本群图片；"
                "请使用 --source 指定 ImageGen 明确返回的 PNG 绝对路径"
            )
        selected = candidates[0]

    output_path = store.image_path(group_name, run_date)
    regeneration = bool(marker.get("regeneration"))
    existing_ok, _ = verify_image(output_path)
    if regeneration:
        temp_path = store.regenerating_image_path(group_name, run_date)
        previous_path = store.previous_image_path(group_name, run_date)
        copy_generated_image(selected, temp_path)
        temp_ok, temp_detail = verify_image(temp_path)
        if not temp_ok:
            temp_path.unlink(missing_ok=True)
            raise ValueError(f"新图片临时落盘验证失败：{temp_detail}")
        if existing_ok:
            shutil.copy2(output_path, previous_path)
        temp_path.replace(output_path)
    elif not existing_ok:
        copy_generated_image(selected, output_path)
    ok, detail = verify_image(output_path)
    if not ok:
        raise ValueError(f"日报图片落盘验证失败：{detail}")

    current_status = str(run.get("status") or "")
    next_status = SENT if current_status == SENT else READY_TO_SEND
    imagegen_ms = _marker_elapsed_ms(marker)
    stage_timings = dict(run.get("stage_timings") or {})
    stage_timings["imagegen_ms"] = imagegen_ms
    fields: dict[str, Any] = {
        "status": next_status,
        "image_status": "regenerated" if regeneration else "success",
        "image_error": None,
        "image_size_bytes": output_path.stat().st_size,
        "image_format": detect_image_format(output_path),
        "imagegen_ms": imagegen_ms,
        "image_generated_at": datetime.now().astimezone().isoformat(),
        "stage_timings": stage_timings,
    }
    if regeneration:
        fields.update(
            image_regen_status="ready_for_review",
            image_regen_error="",
            image_regen_finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            image_regenerated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            desktop_regen_requested=False,
            send_hold=True,
            needs_manual_send=True,
            text_sent_at="",
        )
    else:
        fields.update(failed_stage=None, error=None, error_type=None)
    updated = store.update(group_name, run_date, **fields)
    marker_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "group_name": group_name,
        "run_date": run_date,
        "status": updated["status"],
        "output_path": str(output_path.resolve()),
        "size_bytes": output_path.stat().st_size,
    }


def verify_task(store: RunStore, group_name: str, run_date: str) -> dict[str, Any]:
    run = _existing_run(store, group_name, run_date)
    output_path = store.image_path(group_name, run_date)
    ok, detail = verify_image(output_path)
    return {
        "ok": ok,
        "group_name": group_name,
        "run_date": run_date,
        "status": run.get("status"),
        "output_path": str(output_path.resolve()),
        "detail": detail,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GroupBrief Codex ImageGen 自动化交接脚本")
    sub = parser.add_subparsers(dest="command", required=True)

    pending = sub.add_parser("pending", help="列出昨天报告归属日待生图任务（默认最多 5 个）")
    date_group = pending.add_mutually_exclusive_group()
    date_group.add_argument("--date", help="指定运行日期 YYYY-MM-DD（保留原有运行日过滤语义）")
    date_group.add_argument("--report-date", help="指定报告归属日 YYYY-MM-DD")
    date_group.add_argument("--all", action="store_true", help="扫描所有日期（手动排障用）")
    pending.add_argument("--limit", type=int, default=5, help="最多返回任务数（1-20）")

    for name, help_text in (
        ("begin", "在调用 ImageGen 前记录输出目录快照"),
        ("adopt", "认领本次 ImageGen 新增图片并更新 run.json"),
        ("verify", "只读验证日报图片和任务状态"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--group", required=True, help="run.json 中的完整群名")
        command.add_argument("--date", required=True, help="运行日期 YYYY-MM-DD")
        if name == "adopt":
            command.add_argument("--source", type=Path, help="可选；ImageGen 返回的本地 PNG 路径")
    return parser


def main() -> int:
    args = _parser().parse_args()
    settings = get_settings()
    store = RunStore(settings.output_dir)
    try:
        if args.command == "pending":
            if not 1 <= args.limit <= 20:
                raise ValueError("--limit 必须在 1 到 20 之间")
            if args.all:
                query_type = "all"
                query_date = "ALL"
                run_date = None
                report_date = None
            elif args.date:
                query_type = "run_date"
                query_date = validate_run_date(args.date)
                run_date = query_date
                report_date = None
            else:
                query_type = "report_date"
                report_date = validate_run_date(args.report_date) if args.report_date else _default_report_date(settings)
                query_date = report_date
                run_date = None
            tasks = collect_pending(store, run_date, args.limit, report_date=report_date)
            _json_print({
                "ok": True,
                "query_type": query_type,
                "query_date": query_date,
                "count": len(tasks),
                "tasks": tasks,
            })
            return 0
        if args.command == "begin":
            _json_print(begin_task(store, _generated_images_dir(), args.group, args.date))
            return 0
        if args.command == "adopt":
            _json_print(adopt_image(store, args.group, args.date, args.source))
            return 0
        if args.command == "verify":
            result = verify_task(store, args.group, args.date)
            _json_print(result)
            return 0 if result["ok"] else 3
    except (OSError, ValueError) as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
