"""P9 恢复与完整性检查。

- scan_incomplete：找出未到终态（未 SENT/FAILED）的 run，供启动时重跑；
- verify_output：检查每个 run 的输出文件完整性；
- SENT 绝不重发（见 DailyPipeline.send_due 的 sent_at 检查）；
- IMAGE_READY 跳过重复生图（见 DailyPipeline 防重复逻辑）。
"""

from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger
from app.v2.constants import (
    CORRUPT,
    FILE_IMAGE,
    FILE_PROMPT,
    FILE_RANKING_TXT,
    FAILED,
    IMAGE_READY,
    READY_TO_SEND,
    RUN_STATE_CORRUPT,
    SENT,
)
from app.v2.run_store import RunStore

logger = get_logger("groupbrief.pipeline")

# 需要核心输出文件完整的状态
_REQUIRED_FILES = {
    "DATA_READY": ["messages.json"],
    "RANKING_READY": ["messages.json", "ranking.json", "ranking.txt"],
    "PROMPT_READY": ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"],
    IMAGE_READY: ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt", "daily_image.png"],
    READY_TO_SEND: ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"],
    SENT: ["messages.json", "ranking.json", "ranking.txt", "image_prompt.txt"],
}


def _image_enabled(run: dict) -> bool:
    """读取运行时图片开关；旧 run 缺字段时保守要求图片。"""
    value = run.get("image_enabled")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off"}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    # 旧版 run 没有 image_enabled，不能假定图片关闭，避免误报完整。
    return True


def _required_files(status: str, run: dict) -> list[str]:
    required = list(_REQUIRED_FILES.get(status, []))
    if status in (READY_TO_SEND, SENT) and _image_enabled(run):
        required.append(FILE_IMAGE)
    return required


def scan_incomplete(
    store: RunStore,
    run_date: str | None = None,
    *,
    runs: list[dict] | None = None,
) -> list[dict]:
    """找出未到终态的 run（需要恢复/重跑的）。

    排除 SENT（绝不重发）与 FAILED（保留错误，手动重跑）。
    每个结果附 recovery_type：
      - "send"    生成已齐备（IMAGE_READY/READY_TO_SEND），应触发发送；
      - "generate"生成中断（PENDING~PROMPT_READY），应重新生成。
    返回按更新时间排序的列表。
    """
    incomplete: list[dict] = []
    source_runs = store.list_runs(run_date) if runs is None else runs
    for run in source_runs:
        status = run.get("status", "")
        if status == CORRUPT:
            item = dict(run)
            item["recovery_type"] = "manual_review"
            incomplete.append(item)
            continue
        if run.get("prompt_hold") or run.get("prompt_operation_status") == "unknown":
            item = dict(run)
            item["recovery_type"] = "manual_review"
            item["error_type"] = "PROMPT_RESULT_UNKNOWN"
            incomplete.append(item)
            continue
        if run.get("send_hold_reason") == "SEND_RESULT_UNKNOWN" or run.get("send_state") == "unknown":
            item = dict(run)
            item["recovery_type"] = "manual_review"
            item["error_type"] = "SEND_RESULT_UNKNOWN"
            incomplete.append(item)
            continue
        if status in (SENT, FAILED):
            continue
        item = dict(run)
        item["recovery_type"] = (
            "send" if status in (IMAGE_READY, READY_TO_SEND) else "generate"
        )
        incomplete.append(item)
    incomplete.sort(key=lambda r: r.get("updated_at", ""))
    return incomplete


def verify_output(
    store: RunStore,
    run_date: str | None = None,
    *,
    runs: list[dict] | None = None,
) -> list[dict]:
    """检查所有 run 的输出文件完整性，返回 [{group_name, run_date, status, missing, ok}]。"""
    results: list[dict] = []
    source_runs = store.list_runs(run_date) if runs is None else runs
    for run in source_runs:
        status = run.get("status", "")
        if status == CORRUPT:
            results.append(
                {
                    "group_name": run.get("group_name", "未知群"),
                    "run_date": run.get("run_date", ""),
                    "status": CORRUPT,
                    "missing": [],
                    "ok": False,
                    "error_type": RUN_STATE_CORRUPT,
                    "detail": "运行状态文件损坏，需人工复核",
                }
            )
            continue
        required = _required_files(status, run)
        missing = []
        for f in required:
            path = store.group_dir(run["group_name"], run["run_date"]) / f
            if not path.exists() or path.stat().st_size == 0:
                missing.append(f)
        results.append(
            {
                "group_name": run["group_name"],
                "run_date": run["run_date"],
                "status": status,
                "missing": missing,
                "ok": len(missing) == 0,
            }
        )
    return results


def count_unsent_runs(store: RunStore) -> int:
    """统计非 SENT 的 run 数量（状态提示用）。"""
    return sum(1 for r in store.list_runs() if r.get("status") != SENT)


def recover_incomplete(
    store: RunStore,
    group_ids: list[int] | None = None,
    run_date: str | None = None,
) -> list[dict]:
    """重跑未完成群（异常退出后恢复）。调用方传入可用的 DailyPipeline。

    返回恢复操作结果列表。此函数只收集任务，实际重跑交给 DailyPipeline
    的 generate（防重复保证已到终态的跳过）。
    """
    from app.pipeline.daily_pipeline import DailyPipeline

    incomplete = scan_incomplete(store, run_date)
    if not incomplete:
        return [{"status": "ok", "detail": "无未完成任务"}]
    results: list[dict] = []
    pipeline = None
    for run in incomplete:
        group_name = run["group_name"]
        if run.get("recovery_type") == "manual_review":
            error_type = str(run.get("error_type") or RUN_STATE_CORRUPT)
            results.append(
                {
                    "group_name": group_name,
                    "status": "blocked",
                    "error_type": error_type,
                    "detail": (
                        "AI 调用结果未知，需人工复核"
                        if error_type == "PROMPT_RESULT_UNKNOWN"
                        else "微信发送结果未知，需人工核对后消歧"
                        if error_type == "SEND_RESULT_UNKNOWN"
                        else "运行状态文件损坏，需人工复核"
                    ),
                }
            )
            continue
        if pipeline is None:
            pipeline = DailyPipeline()
        # 找到群 id（按显示名）
        gid = _find_group_id_by_name(group_name)
        if gid is None:
            results.append({"group_name": group_name, "status": "skipped", "detail": "群已停用/不存在"})
            continue
        if run.get("recovery_type") == "send":
            r = pipeline.force_send(gid, run["run_date"])
        else:
            r = pipeline.force_generate(gid, run["run_date"])
        results.append({"group_name": group_name, "status": r.get("status"), "detail": r.get("error") or ""})
    return results


def _find_group_id_by_name(group_name: str) -> int | None:
    from sqlmodel import Session, select

    from app.db import repository as repo
    from app.db.models import Group

    with Session(repo.engine) as session:
        group = session.exec(select(Group).where(Group.display_name == group_name)).first()
        return group.id if group else None
