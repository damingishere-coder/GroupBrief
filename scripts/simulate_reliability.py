"""GroupBrief 30 天无人值守确定性故障注入仿真（不调用任何外部服务）。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image

from app.ai.prompt_builder_types import PromptOutput
from app.config.settings import Settings
from app.data_sources.base import DataSourceStatus, FetchResult, V2Message, WeChatDataSource
from app.db.models import Group
from app.image.image_task import ImageTaskResult
from app.pipeline.daily_pipeline import DailyPipeline
from app.scheduler.daily_v2_job import (
    DailyScheduleState,
    _compact_results,
    _generation_results_terminal,
    _generation_status,
)
from app.sender.base import SendResult, WechatSender
from app.services.group_name_sync import GroupNameSyncReport
from app.v2.constants import SENT
from app.v2.run_store import RunStore


class FaultPlan:
    RATES = {
        "network_timeout": 0.05,
        "ai_invalid_format": 0.05,
        "image_generation_failed": 0.05,
        "image_download_failed": 0.03,
        "send_failed": 0.03,
        "program_interrupt": 0.03,
        "duplicate_start": 0.08,
    }

    def __init__(self, seed: int):
        self.seed = int(seed)
        self.seen: Counter[tuple[str, str]] = Counter()
        self.injected: Counter[str] = Counter()

    def selected(self, stage: str, key: str) -> bool:
        digest = hashlib.sha256(f"{self.seed}|{stage}|{key}".encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big") / float(2**64)
        return value < self.RATES[stage]

    def once(self, stage: str, key: str) -> bool:
        identity = (stage, key)
        self.seen[identity] += 1
        if self.seen[identity] != 1 or not self.selected(stage, key):
            return False
        self.injected[stage] += 1
        return True


class SimulationSource(WeChatDataSource):
    name = "simulation_source"

    def __init__(self, faults: FaultPlan):
        self.faults = faults
        self.calls: Counter[str] = Counter()

    def fetch_messages(self, group_id, start_time, end_time):
        key = f"{end_time.date().isoformat()}|{group_id}"
        self.calls[key] += 1
        if self.faults.once("program_interrupt", f"fetch|{key}"):
            raise RuntimeError("simulated process interruption after task checkpoint")
        if self.faults.once("network_timeout", key):
            return FetchResult(
                [],
                DataSourceStatus.READ_FAILED,
                "simulated network timeout",
                "MESSAGE_FETCH_FAILED",
            )
        return FetchResult(
            [
                V2Message(
                    message_id=f"{key}-1",
                    group_id=str(group_id),
                    group_name=str(group_id),
                    sender_id="member-a",
                    sender_name="群友甲",
                    timestamp=start_time + timedelta(hours=10),
                    content="今天讨论项目进展 500 万和稳定性",
                ),
                V2Message(
                    message_id=f"{key}-2",
                    group_id=str(group_id),
                    group_name=str(group_id),
                    sender_id="member-b",
                    sender_name="群友乙",
                    timestamp=start_time + timedelta(hours=11),
                    content="补充了第二个话题和验证结果",
                ),
            ],
            DataSourceStatus.OK,
            "simulation ok",
            meta={"provider_chain": ["simulation"]},
        )


class SimulationPrompt:
    def __init__(self, faults: FaultPlan):
        self.faults = faults
        self.calls: Counter[str] = Counter()

    def build(self, data):
        key = f"{data.run_date}|{data.group_id}"
        self.calls[key] += 1
        if self.faults.once("ai_invalid_format", key):
            return PromptOutput(False, error="simulated invalid AI schema", model="simulation-ai")
        selection = {
            "topic_selection_version": "4.0",
            "selected_topic_ids": ["topic-01"],
            "selected_count": 1,
            "candidates": [
                {
                    "topic_id": "topic-01",
                    "selected": True,
                    "title": "稳定性进展",
                    "summary": "群友讨论当天进展和验证结果",
                    "message_ids": [data.messages[0].message_id],
                    "quotes": ["今天讨论项目进展 500 万和稳定性"],
                    "visible_participants": ["群友甲"],
                }
            ],
        }
        return PromptOutput(
            True,
            "【任务】\n生成群聊漫画\n【主标题】\n今日热聊",
            model="simulation-ai",
            meta={
                "api_model": "simulation-ai",
                "api_call_count": 1,
                "chunk_count": 1,
                "topic_selection": selection,
            },
        )


class SimulationImageGenerator:
    def __init__(self, faults: FaultPlan):
        self.faults = faults
        self.calls: Counter[str] = Counter()
        self.settings = Settings(_env_file=None)

    def generate(self, prompt_file: Path, output_path: Path, **_kwargs):
        key = f"{output_path.parent.name}|{output_path.parent.parent.name}"
        self.calls[key] += 1
        if self.faults.once("image_generation_failed", key):
            return ImageTaskResult(
                False,
                error="simulated image API 5xx",
                detail={"error_code": "API_5XX", "outcome_unknown": False},
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.faults.once("image_download_failed", key):
            output_path.write_bytes(b"truncated-image")
            return ImageTaskResult(True, image_path=output_path, detail={"attempt_count": 1})
        Image.new("RGB", (64, 96), "white").save(output_path, format="PNG")
        return ImageTaskResult(
            True,
            image_path=output_path,
            detail={"attempt_count": 1, "receipt_source": "simulation"},
        )


class SimulationSender(WechatSender):
    name = "simulation_sender"

    def __init__(self, faults: FaultPlan):
        self.faults = faults
        self.text_calls: Counter[str] = Counter()
        self.image_calls: Counter[str] = Counter()
        self.text_submissions: Counter[str] = Counter()
        self.image_submissions: Counter[str] = Counter()

    @staticmethod
    def _key(target: str, payload) -> str:
        run_date = Path(payload).parent.name if not isinstance(payload, str) or Path(payload).is_file() else ""
        return f"{run_date}|{target}" if run_date else str(target)

    def health_check(self):
        return True, "simulation ok"

    def send_text(self, target: str, text: str):
        match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", text)
        run_date = match.group(0) if match else "unknown-date"
        key = f"{run_date}|{target}"
        self.text_calls[key] += 1
        occurrence_key = f"text|{target}|{self.text_calls[key]}"
        if self.faults.once("send_failed", occurrence_key):
            return SendResult(False, "simulated pre-submit send failure", submitted=False)
        self.text_submissions[key] += 1
        return SendResult(
            True,
            "simulation text sent",
            datetime.now().astimezone().isoformat(),
            submitted=True,
            verification_level="simulation",
        )

    def send_image(self, target: str, image_path):
        key = f"{Path(image_path).parent.name}|{target}"
        self.image_calls[key] += 1
        if self.faults.once("send_failed", f"image|{key}"):
            return SendResult(False, "simulated pre-submit image failure", submitted=False)
        self.image_submissions[key] += 1
        return SendResult(
            True,
            "simulation image sent",
            datetime.now().astimezone().isoformat(),
            submitted=True,
            verification_level="simulation",
        )


def _pipeline(
    settings: Settings,
    store: RunStore,
    groups: list[Group],
    source: SimulationSource,
    prompt: SimulationPrompt,
    image: SimulationImageGenerator,
    sender: SimulationSender,
) -> DailyPipeline:
    pipeline = DailyPipeline(
        settings=settings,
        data_source=source,
        prompt_builder=prompt,
        image_generator=image,
        sender=sender,
        store=store,
    )
    pipeline._load_groups = lambda group_ids=None: [
        group for group in groups if not group_ids or group.id in group_ids
    ]
    pipeline._get_group = lambda group_id: next(
        (group for group in groups if group.id == group_id),
        None,
    )
    pipeline._sync_group_names_safe = lambda group_ids=None: GroupNameSyncReport(
        status="cached",
        source="simulation",
        checked=len(group_ids or groups),
    )
    return pipeline


def _scheduler_generate(
    pipeline: DailyPipeline,
    state: DailyScheduleState,
    run_date: str,
) -> list[dict]:
    current = state.load(run_date)
    fields = {
        "generation_status": "resuming" if current.get("generation_started_at") else "running",
        "generation_started_at": current.get("generation_started_at") or datetime.now().astimezone().isoformat(),
        "generation_hold": False,
    }
    state.update(run_date, **fields)
    results = pipeline.generate_all(run_date=run_date)
    completion = {
        "generation_status": _generation_status(results),
        "generation_results": _compact_results(results),
        "generation_invocation_completed_at": datetime.now().astimezone().isoformat(),
    }
    if _generation_results_terminal(results):
        completion["generation_completed_at"] = datetime.now().astimezone().isoformat()
    state.update(run_date, **completion)
    pipeline._write_runtime_status_safe([run_date])
    return results


def run_simulation(
    *,
    days: int = 30,
    groups_count: int = 6,
    seed: int = 20260827,
    workdir: Path,
) -> dict:
    faults = FaultPlan(seed)
    settings = Settings(
        _env_file=None,
        app_timezone="Asia/Shanghai",
        generation_group_concurrency=min(groups_count, 5),
        image_generation_concurrency=2,
        wechat_fetch_concurrency=2,
        ai_request_concurrency=2,
        wechat_late_send_window_minutes=30,
    )
    store = RunStore(workdir / "output")
    state = DailyScheduleState(store.root)
    groups = [
        Group(
            id=index,
            display_name=f"sim-group-{index:02d}",
            wechat_group_id=f"sim-{index}@chatroom",
            wechat_group_name=f"sim-group-{index:02d}",
            send_target=f"sim-target-{index:02d}",
            enabled=True,
            image_enabled=True,
            wechat_send_enabled=True,
            send_time="08:30",
        )
        for index in range(1, groups_count + 1)
    ]
    source = SimulationSource(faults)
    prompt = SimulationPrompt(faults)
    image = SimulationImageGenerator(faults)
    sender = SimulationSender(faults)

    import app.pipeline.generation_stages as generation_stages

    original_retry_is_due = generation_stages.retry_is_due
    generation_stages.retry_is_due = lambda _run: True
    try:
        start = date(2026, 8, 27) - timedelta(days=days - 1)
        all_dates: list[str] = []
        for offset in range(days):
            run_date = (start + timedelta(days=offset)).isoformat()
            all_dates.append(run_date)
            pipeline = _pipeline(settings, store, groups, source, prompt, image, sender)
            for _ in range(5):
                _scheduler_generate(pipeline, state, run_date)
                if state.load(run_date).get("generation_completed_at"):
                    break
            # 随机“进程在发送扫描前中断”：新实例随后通过历史恢复继续。
            if not faults.once("program_interrupt", f"before-send|{run_date}"):
                now = datetime.fromisoformat(f"{run_date}T09:00:00+08:00")
                pipeline.send_due_for_dates([run_date], now=now, recovery=True)
            if faults.once("duplicate_start", run_date):
                duplicate = _pipeline(settings, store, groups, source, prompt, image, sender)
                _scheduler_generate(duplicate, state, run_date)

            recovery = _pipeline(settings, store, groups, source, prompt, image, sender)
            now = datetime.fromisoformat(f"{run_date}T09:10:00+08:00")
            for _ in range(4):
                recovery.send_due_for_dates(all_dates, now=now, recovery=True)

        runs = store.list_runs()
        expected = days * groups_count
        sent = [run for run in runs if run.get("status") == SENT]
        manual_holds = [run for run in runs if run.get("execution_state") == "HOLD_MANUAL"]
        retry_pending = [run for run in runs if run.get("execution_state") == "WAIT_RETRY"]
        failed_final = [run for run in runs if run.get("execution_state") == "FAILED_FINAL"]
        task_loss = max(expected - len(runs), 0)
        duplicate_images = sum(max(count - 1, 0) for count in image.calls.values())
        duplicate_image_sends = sum(max(count - 1, 0) for count in sender.image_submissions.values())
        duplicate_text_sends = sum(max(count - 1, 0) for count in sender.text_submissions.values())
        scheduler_incomplete = sum(
            1 for run_date in all_dates if not state.load(run_date).get("generation_completed_at")
        )
        runtime_reports = sum(
            int((workdir / "runtime" / run_date / "status.json").is_file())
            for run_date in all_dates
        )
        result = {
            "seed": seed,
            "days": days,
            "groups": groups_count,
            "expected_tasks": expected,
            "runs_found": len(runs),
            "sent": len(sent),
            "manual_holds": len(manual_holds),
            "failed_final": len(failed_final),
            "retry_pending": len(retry_pending),
            "task_loss": task_loss,
            "duplicate_external_image_calls": duplicate_images,
            "duplicate_successful_image_sends": duplicate_image_sends,
            "duplicate_successful_text_sends": duplicate_text_sends,
            "scheduler_incomplete_dates": scheduler_incomplete,
            "runtime_reports": runtime_reports,
            "injected": dict(sorted(faults.injected.items())),
            "source_max_attempts": max(source.calls.values(), default=0),
            "prompt_max_attempts": max(prompt.calls.values(), default=0),
            "image_max_attempts": max(image.calls.values(), default=0),
            "ok": (
                len(runs) == expected
                and len(sent) == expected
                and not manual_holds
                and not failed_final
                and not retry_pending
                and task_loss == 0
                and duplicate_images == 0
                and duplicate_image_sends == 0
                and duplicate_text_sends == 0
                and scheduler_incomplete == 0
                and runtime_reports == days
            ),
        }
        return result
    finally:
        generation_stages.retry_is_due = original_retry_is_due


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--groups", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()
    if args.days < 1 or args.groups < 1:
        parser.error("--days 和 --groups 必须大于 0")

    if args.workdir is not None:
        args.workdir.mkdir(parents=True, exist_ok=True)
        result = run_simulation(
            days=args.days,
            groups_count=args.groups,
            seed=args.seed,
            workdir=args.workdir.resolve(),
        )
    else:
        with tempfile.TemporaryDirectory(prefix="groupbrief-reliability-") as temp:
            result = run_simulation(
                days=args.days,
                groups_count=args.groups,
                seed=args.seed,
                workdir=Path(temp),
            )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
