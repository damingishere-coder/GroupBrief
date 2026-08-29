"""V2 每日全流程流水线（P7）。

生成阶段（默认 00:15，run_date 决定周期）：
    PENDING → 首次取数/复用当天快照(messages.json) → DATA_READY → 排行(ranking.json/txt)
    → RANKING_READY → Codex GPT / DeepSeek 备用(image_prompt.txt) → PROMPT_READY
    → Codex 受控并发生图(daily_image.png) → IMAGE_READY → READY_TO_SEND
发送阶段（每群 send_time）：
    READY_TO_SEND/IMAGE_READY → 发排行榜文字 → 发图片 → SENT

约束：
- 每个群独立状态；某群失败不阻塞其他群；
- 生图阶段默认最多 2 路并发，每个结果按独立 job_id 归属；
- 同一群同一统计周期已到终态则跳过（force 可重跑）；
- 同一日报日期的消息默认只读取一次；显式 refresh_messages 只覆盖当天快照，不连带重建 Prompt 或生图；
- SENT 绝不重复发送（force_send 允许重发内容）。
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.ai.concurrency import normalized_limit
from app.ai.prompt_builder import GroupSummaryImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.ai.speaker_attribution import build_attribution_contract
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.data_sources.base import V2Message, WeChatDataSource
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.data_sources.history_provider import HistoryProviderDataSource
from app.data_sources.resilient import ResilientWeChatDataSource
from app.db import repository as repo
from app.db.models import Group
from app.db.resilience import run_with_sqlite_retry
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import ImageJob
from app.pipeline.delivery_stages import DeliveryStages
from app.pipeline.generation_stages import GenerationStages
from app.pipeline.image_stages import ImageStages
from app.ranking.engine import RankingEngine
from app.ranking.renderer import RankingRenderer
from app.scheduler.period import PeriodResolver, PeriodWindow
from app.scheduler.runtime_status import write_daily_status
from app.sender.base import WechatSender
from app.sender.wechat_native import create_wechat_sender
from app.services.generation_runtime import generation_mutex
from app.services.group_provider_config import (
    normalize_history_provider,
    resolve_group_ai_settings,
)
from app.services.group_name_sync import (
    GroupNameSyncReport,
    GroupNameSyncService,
    send_target_mode,
)
from app.providers.history.wechat_cli import WechatCliProvider
from app.v2.constants import (
    CORRUPT,
    FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    PROMPT_FAILED,
    PROMPT_READY,
    READY_TO_SEND,
    RUN_STATE_CORRUPT,
    SENT,
)
from app.v2.run_store import RunStore, validate_run_date

logger = get_logger("groupbrief.pipeline")


class DailyPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        data_source: WeChatDataSource | None = None,
        ranking_engine: RankingEngine | None = None,
        renderer: RankingRenderer | None = None,
        prompt_builder: GroupSummaryImagePromptBuilder | None = None,
        image_generator=None,
        sender: WechatSender | None = None,
        store: RunStore | None = None,
        dry_run: bool = False,
    ):
        self.settings = settings or get_settings()
        self._data_source_injected = data_source is not None
        self.data_source = data_source or ResilientWeChatDataSource(
            WeChatDataAnalysisSource(self.settings),
            self.settings,
        )
        self.period_resolver = PeriodResolver()
        self.ranking_engine = ranking_engine or RankingEngine()
        self.renderer = renderer or RankingRenderer()
        self._prompt_builder_injected = prompt_builder is not None
        self.prompt_builder = prompt_builder or GroupSummaryImagePromptBuilder(
            self.settings,
            summary_settings=self.settings,
        )
        self._data_source_cache: dict[str, WeChatDataSource] = {}
        self._prompt_builder_cache: dict[tuple[str, ...], GroupSummaryImagePromptBuilder] = {}
        self.image_generator = image_generator or CodexImageGenerator(self.settings)
        self.sender = sender or create_wechat_sender(settings=self.settings, dry_run=dry_run)
        self.store = store or RunStore(self.settings.output_dir)
        self.dry_run = dry_run
        self._last_name_sync_report: GroupNameSyncReport | None = None

    # ================= 生成阶段 =================

    def generate_all(
        self,
        run_date: str | None = None,
        group_ids: list[int] | None = None,
        force: bool = False,
        refresh_messages: bool = False,
        group_overrides: dict[int, dict] | None = None,
        *,
        acquire_lock: bool = True,
    ) -> list[dict]:
        if acquire_lock:
            with generation_mutex():
                return self.generate_all(
                    run_date=run_date,
                    group_ids=group_ids,
                    force=force,
                    refresh_messages=refresh_messages,
                    group_overrides=group_overrides,
                    acquire_lock=False,
                )
        requested_date = parse_date(run_date)
        if run_date is not None and requested_date is None:
            return [{
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }]
        base_window = self.period_resolver.resolve(
            run_date=requested_date,
            timezone=self.settings.app_timezone,
            schedule_rule="daily_previous_day",
        )
        run_date_str = base_window.run_date.isoformat()
        self._last_name_sync_report = self._sync_group_names_safe(group_ids)
        groups = self._load_groups(group_ids)
        if group_overrides:
            allowed_override_fields = {
                "wechat_group_id", "wechat_group_name", "provider_preference",
                "schedule_rule", "send_time", "summary_provider", "summary_model",
                "prompt_provider", "prompt_model", "image_enabled", "ranking_template",
                "ranking_count_policy", "sender_name_policy",
                "image_prompt_template", "image_theme", "image_theme_custom",
                "image_prompt_override", "send_target",
            }
            groups = [
                Group.model_validate(
                    {
                        **group.model_dump(),
                        **{
                            key: value
                            for key, value in group_overrides.get(
                                int(group.id or 0), {}
                            ).items()
                            if key in allowed_override_fields
                        },
                    }
                )
                for group in groups
            ]
        if not groups:
            return [{"status": "no_groups", "reason": "无启用群"}]
        scheduled: list[tuple[Group, PeriodWindow]] = []
        for group in groups:
            window = self.period_resolver.resolve(
                run_date=base_window.run_date,
                timezone=self.settings.app_timezone,
                schedule_rule=str(group.schedule_rule or "daily_previous_day"),
            )
            if window.should_run:
                scheduled.append((group, window))
        if not scheduled:
            return [{"status": "no_groups", "reason": "当日没有符合群级统计规则的任务"}]
        groups = [item[0] for item in scheduled]
        windows = [item[1] for item in scheduled]

        group_limit = normalized_limit(self.settings.generation_group_concurrency, 5)
        image_limit = normalized_limit(self.settings.image_generation_concurrency, 2)
        logger.info(
            "开始并行生成：groups=%d group_limit=%d fetch_limit=%d ai_limit=%d image_limit=%d",
            len(groups),
            group_limit,
            normalized_limit(self.settings.wechat_fetch_concurrency, 1),
            normalized_limit(self.settings.ai_request_concurrency, 6),
            image_limit,
        )
        if len(groups) == 1:
            group = groups[0]
            window = windows[0]
            try:
                if refresh_messages:
                    result = self._generate_one_safe(
                        group,
                        window,
                        run_date_str,
                        force,
                        refresh_messages=True,
                    )
                else:
                    # 保留历史 4 参数调用形态，兼容现有注入点与测试替身。
                    result = self._generate_one_safe(group, window, run_date_str, force)
            except Exception as exc:
                logger.exception("群 %s worker 未捕获异常，已隔离", self._group_name(group))
                result = self._record_group_failure(group, run_date_str, exc, "unexpected")
            return [self._run_image_when_ready_safe(group, result, run_date_str, force)]
        else:
            with ThreadPoolExecutor(
                max_workers=min(group_limit, len(groups)),
                thread_name_prefix="groupbrief-v2-group",
            ) as executor, ThreadPoolExecutor(
                max_workers=min(image_limit, len(groups)),
                thread_name_prefix="groupbrief-v2-image",
            ) as image_executor:
                future_indexes = {}
                for index, group in enumerate(groups):
                    window = windows[index]
                    if refresh_messages:
                        future = executor.submit(
                            self._generate_one_safe,
                            group,
                            window,
                            run_date_str,
                            force,
                            refresh_messages=True,
                        )
                    else:
                        # 保留历史 4 参数调用形态，兼容现有注入点与测试替身。
                        future = executor.submit(
                            self._generate_one_safe,
                            group,
                            window,
                            run_date_str,
                            force,
                        )
                    future_indexes[future] = index
                image_future_indexes = {}
                for future in as_completed(future_indexes):
                    result_index = future_indexes[future]
                    group = groups[result_index]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.exception("群 %s worker 未捕获异常，已隔离", self._group_name(group))
                        result = self._record_group_failure(
                            group, run_date_str, exc, "unexpected"
                        )
                    image_future = image_executor.submit(
                        self._run_image_when_ready_safe,
                        group,
                        result,
                        run_date_str,
                        force,
                    )
                    image_future_indexes[image_future] = result_index

                results_by_index: dict[int, dict] = {}
                for image_future in as_completed(image_future_indexes):
                    result_index = image_future_indexes[image_future]
                    group = groups[result_index]
                    try:
                        results_by_index[result_index] = image_future.result()
                    except Exception as exc:
                        logger.exception(
                            "群 %s 图片 worker 未捕获异常，已隔离",
                            self._group_name(group),
                        )
                        results_by_index[result_index] = self._record_group_failure(
                            group, run_date_str, exc, "image"
                        )

        # 生图按 Prompt 完成顺序启动，API 结果仍按群配置顺序返回。
        return [results_by_index[index] for index in range(len(groups))]

    @staticmethod
    def _group_name(group: Group) -> str:
        return group.display_name or group.wechat_group_name

    def _prompt_operation_hash(
        self,
        data: PromptInput,
        prompt_settings: Settings | None = None,
        summary_settings: Settings | None = None,
    ) -> str:
        """生成稳定输入指纹；不把 API Key 或原始输入写入运行状态。"""
        payload = dict(vars(data))
        payload["messages"] = [
            message.to_dict() if hasattr(message, "to_dict") else str(message)
            for message in data.messages
        ]
        selected_prompt = prompt_settings or self.settings
        selected_summary = summary_settings or self.settings
        payload["provider_config"] = {
            "summary": {
                "primary": selected_summary.summary_provider_primary,
                "fallback": selected_summary.summary_provider_fallback,
                "codex_model": selected_summary.codex_summary_model,
                "deepseek_model": selected_summary.ai_model,
            },
            "prompt": {
                "primary": selected_prompt.summary_provider_primary,
                "fallback": selected_prompt.summary_provider_fallback,
                "codex_model": selected_prompt.codex_summary_model,
                "deepseek_model": selected_prompt.ai_model,
            },
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _record_group_failure(
        self,
        group: Group,
        run_date: str,
        exc: Exception,
        failed_stage: str,
    ) -> dict:
        """尽力落盘单群未捕获异常；落盘本身失败也不阻塞其他群。"""
        group_name = self._group_name(group)
        detail = str(exc)[:300]
        error_type = (
            IMAGE_GENERATION_FAILED
            if failed_stage == "image"
            else "UNEXPECTED_GENERATION_ERROR"
        )
        try:
            self.store.update(
                group_name,
                run_date,
                status=FAILED,
                failed_stage=failed_stage,
                error=detail,
                error_type=error_type,
            )
        except Exception:
            logger.exception("群 %s 异常状态落盘失败，继续处理其他群", group_name)
        return {
            "group_name": group_name,
            "status": "failed",
            "error_type": error_type,
            "detail": detail,
        }

    def _run_image_when_ready_safe(
        self,
        group: Group,
        result: dict,
        run_date: str,
        force: bool,
    ) -> dict:
        try:
            return self._run_image_when_ready(group, result, run_date, force)
        except Exception as exc:
            logger.exception("群 %s 图片阶段异常，已与其他群隔离", self._group_name(group))
            return self._record_group_failure(group, run_date, exc, "image")

    def _run_image_when_ready(
        self,
        group: Group,
        result: dict,
        run_date: str,
        force: bool,
    ) -> dict:
        """单群 Prompt 就绪后立即提交受控并发生图。"""
        if not result.get("need_image"):
            return result
        group_name = group.display_name or group.wechat_group_name
        logger.info("群 %s Prompt 已就绪，立即进入受控并发生图", group_name)
        job = self._make_image_job(group, run_date, force)
        return self._run_image_jobs([job], run_date)[0]

    def _generate_one_safe(
        self,
        group: Group,
        window,
        run_date: str,
        force: bool,
        refresh_messages: bool = False,
    ) -> dict:
        group_name = group.display_name or group.wechat_group_name
        try:
            return self._generate_one(
                group, window, run_date, force, refresh_messages=refresh_messages
            )
        except Exception as exc:
            logger.exception("群 %s 生成异常，已与其他群隔离", group_name)
            self.store.update(
                group_name,
                run_date,
                status=FAILED,
                failed_stage="unexpected",
                error=str(exc)[:300],
                error_type="UNEXPECTED_GENERATION_ERROR",
            )
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "UNEXPECTED_GENERATION_ERROR",
                "detail": str(exc)[:300],
            }

    def _generate_one(
        self,
        group: Group,
        window,
        run_date: str,
        force: bool,
        *,
        refresh_messages: bool = False,
        reuse_persisted_topic_selection: bool = False,
    ) -> dict:
        """保留原注入点；单群生成由显式阶段执行器负责。"""
        data_source = self._data_source_for_group(group)
        prompt_settings, prompt_config = resolve_group_ai_settings(
            self.settings,
            group,
            capability="prompt",
        )
        summary_settings, summary_config = resolve_group_ai_settings(
            self.settings,
            group,
            capability="summary",
        )
        self.store.update(
            self._group_name(group),
            run_date,
            config_version=1,
            history_provider_requested=(
                normalize_history_provider(group.provider_preference) or self.data_source.name
            ),
            prompt_provider_requested=prompt_config["provider"],
            prompt_model_requested=prompt_config["model"],
            prompt_config_inherited=prompt_config["inherited"],
            summary_provider_requested=summary_config["provider"],
            summary_model_requested=summary_config["model"],
            summary_config_inherited=summary_config["inherited"],
        )
        prompt_builder = self._prompt_builder_for_group(
            summary_settings,
            prompt_settings,
        )
        return GenerationStages(
            settings=prompt_settings,
            data_source=data_source,
            ranking_engine=self.ranking_engine,
            renderer=self.renderer,
            prompt_builder=prompt_builder,
            store=self.store,
            group_name=self._group_name,
            name_sync_audit=self._name_sync_audit,
            get_group=self._get_group,
            prompt_operation_hash=lambda data: self._prompt_operation_hash(
                data,
                prompt_settings,
                summary_settings,
            ),
            save_json=self._save_json,
            load_message_snapshot=self._load_message_snapshot,
            logger=logger,
        ).run(
            group,
            window,
            run_date,
            force,
            refresh_messages=refresh_messages,
            reuse_persisted_topic_selection=reuse_persisted_topic_selection,
        )

    def _data_source_for_group(self, group: Group) -> WeChatDataSource:
        if self._data_source_injected:
            return self.data_source
        selected = normalize_history_provider(group.provider_preference)
        if selected in {"", "wechat_data_analysis"}:
            return self.data_source
        if selected not in self._data_source_cache:
            source = HistoryProviderDataSource(WechatCliProvider(settings=self.settings))
            self._data_source_cache[selected] = ResilientWeChatDataSource(
                source,
                self.settings,
            )
        return self._data_source_cache[selected]

    def _prompt_builder_for_group(
        self,
        summary_settings: Settings,
        prompt_settings: Settings,
    ) -> GroupSummaryImagePromptBuilder:
        if self._prompt_builder_injected:
            return self.prompt_builder
        key = (
            summary_settings.summary_provider_primary,
            summary_settings.summary_provider_fallback,
            summary_settings.codex_summary_model,
            summary_settings.ai_model,
            prompt_settings.summary_provider_primary,
            prompt_settings.summary_provider_fallback,
            prompt_settings.codex_summary_model,
            prompt_settings.ai_model,
        )
        if key not in self._prompt_builder_cache:
            self._prompt_builder_cache[key] = GroupSummaryImagePromptBuilder(
                prompt_settings,
                summary_settings=summary_settings,
            )
        return self._prompt_builder_cache[key]

    def _make_image_job(self, group: Group, run_date: str, force: bool) -> ImageJob:
        """保留原注入点；图片任务构造由图片阶段负责。"""
        return ImageStages(
            store=self.store,
            image_generator=self.image_generator,
        ).make_job(self._group_name(group), run_date, force)

    def _image_hook(self, job: ImageJob, result: dict) -> None:
        """保留原 hook；图片结果字段和状态推进保持不变。"""
        ImageStages(
            store=self.store,
            image_generator=self.image_generator,
        ).record_result(job, result)

    def _after_image(self, job: ImageJob, run_date: str) -> None:
        ImageStages(
            store=self.store,
            image_generator=self.image_generator,
        ).advance_ready(job, run_date)

    def _run_image_jobs(self, image_jobs: list[ImageJob], run_date: str) -> list[dict]:
        """串行执行图片任务，并以每个 run.json 的最终状态返回结果。"""
        return ImageStages(
            store=self.store,
            image_generator=self.image_generator,
        ).run_jobs(
            image_jobs,
            run_date,
            run_hook=self._image_hook,
            after_hook=self._after_image,
        )

    # ================= 发送阶段 =================

    def send_due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(ZoneInfo(self.settings.app_timezone))
        return self.send_due_for_dates([now.date().isoformat()], now=now, recovery=False)

    def send_due_for_dates(
        self,
        run_dates: list[str],
        *,
        now: datetime | None = None,
        recovery: bool = False,
    ) -> list[dict]:
        """扫描指定日报日期。

        默认 ``send_due`` 仍只处理当天。Watchdog 才能传入历史日期；历史任务
        仍必须通过现有 claim、未知结果锁、目标预检和图片预检。
        """
        now = now or datetime.now(ZoneInfo(self.settings.app_timezone))
        normalized_dates = sorted({validate_run_date(value) for value in run_dates})
        results: list[dict] = []
        groups = self._load_groups()
        due_group_ids: list[int] = []
        for run_date in normalized_dates:
            due_group_ids.extend(
                self._due_sync_group_ids(
                    groups,
                    run_date,
                    now,
                    recovery=recovery,
                )
            )
        due_group_ids = sorted(set(due_group_ids))
        if due_group_ids:
            self._last_name_sync_report = self._sync_group_names_safe(due_group_ids)
            groups = self._load_groups()
        for run_date in normalized_dates:
            report_date = date.fromisoformat(run_date)
            for group in groups:
                if not bool(getattr(group, "wechat_send_enabled", False)):
                    continue
                group_name = group.display_name or group.wechat_group_name
                run = self.store.load_run(group_name, run_date)
                status = run.get("status")
                if status not in (IMAGE_READY, READY_TO_SEND):
                    continue
                if run.get("sent_at"):
                    continue  # 已发送，绝不重复
                if run.get("send_hold"):
                    continue  # unknown / 手工审核必须保持 fail-closed
                send_time = parse_send_time(group.send_time or run.get("send_time", "08:30"))
                due_at = datetime.combine(report_date, send_time, tzinfo=now.tzinfo)
                if now < due_at:
                    continue
                late_window = timedelta(
                    minutes=max(int(self.settings.wechat_late_send_window_minutes), 0)
                )
                if not recovery and now > due_at + late_window:
                    self.store.update(
                        group_name,
                        run_date,
                        send_state="held",
                        send_hold=True,
                        send_hold_reason="MISSED_SEND_WINDOW",
                        needs_manual_send=True,
                        send_error="已超过到点后 30 分钟自动补发窗口，需人工确认",
                        send_error_type="MISSED_SEND_WINDOW",
                        missed_send_window_at=now.isoformat(),
                    )
                    results.append(
                        {
                            "group_name": group_name,
                            "status": "held",
                            "error_type": "MISSED_SEND_WINDOW",
                            "detail": "已超过自动补发窗口，需人工确认",
                        }
                    )
                    continue
                if recovery and report_date < now.date():
                    self.store.update(
                        group_name,
                        run_date,
                        send_state="held",
                        send_hold=True,
                        send_hold_reason="HISTORICAL_SEND_REQUIRES_CONFIRMATION",
                        needs_manual_send=True,
                        send_error="历史任务禁止自动发送，需重新核对目标并人工确认",
                        send_error_type="HISTORICAL_SEND_REQUIRES_CONFIRMATION",
                    )
                    results.append(
                        {
                            "group_name": group_name,
                            "status": "held",
                            "error_type": "HISTORICAL_SEND_REQUIRES_CONFIRMATION",
                            "detail": "历史任务禁止自动发送",
                        }
                    )
                    continue
                if recovery:
                    self.store.update(
                        group_name,
                        run_date,
                        send_recovery=True,
                        send_recovery_checked_at=now.isoformat(),
                    )
                    run = self.store.load_run(group_name, run_date)
                try:
                    result = self._send_one(group, group_name, run, run_date, now)
                except Exception as exc:
                    logger.exception(
                        "群 %s 发送异常，检查是否已进入外部提交窗口",
                        group_name,
                    )
                    result, abort_batch = self._handle_send_exception(
                        group_name,
                        run_date,
                        now,
                        exc,
                    )
                    results.append(result)
                    if abort_batch:
                        logger.error(
                            "微信桌面状态可能未知，停止本批次后续群发送 date=%s",
                            run_date,
                        )
                        self._write_runtime_status_safe(normalized_dates)
                        return results
                    continue
                results.append(result)
        self._write_runtime_status_safe(normalized_dates)
        return results

    def _write_runtime_status_safe(self, run_dates: list[str]) -> None:
        for run_date in run_dates:
            try:
                write_daily_status(self.store, run_date)
            except Exception:
                logger.exception("每日运行报告写入失败：run_date=%s", run_date)

    def _handle_send_exception(
        self,
        group_name: str,
        run_date: str,
        now: datetime,
        exc: Exception,
    ) -> tuple[dict, bool]:
        """隔离明确的提交前异常；未决外部提交则锁单并中止本批次。"""
        detail = f"发送阶段异常：{type(exc).__name__}: {str(exc)[:220]}"
        try:
            run = self.store.load_run(group_name, run_date)
        except Exception as state_exc:
            return (
                {
                    "group_name": group_name,
                    "status": "held",
                    "error_type": "SEND_STATE_UNREADABLE",
                    "detail": f"{detail}；且无法读取发送状态：{type(state_exc).__name__}",
                },
                True,
            )

        unresolved_stage = ""
        for stage in ("image", "text"):
            if (
                run.get(f"{stage}_attempt_started_at")
                and not run.get(f"{stage}_attempt_finished_at")
                and not (run.get(f"{stage}_verified_at") or run.get(f"{stage}_sent_at"))
            ):
                unresolved_stage = stage
                break
        if not unresolved_stage:
            return (
                {
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": "SEND_PRE_SUBMIT_FAILED",
                    "detail": detail,
                },
                False,
            )

        claim_id = str(run.get("send_claim_id") or "")
        marked, _, reason = self.store.mark_send_result_unknown(
            group_name,
            run_date,
            claim_id,
            stage=unresolved_stage,
            detail=detail,
            now=now,
        )
        if not marked:
            detail = f"{detail}；unknown 状态持久化失败（{reason}）"
        return (
            {
                "group_name": group_name,
                "status": "held",
                "error_type": "SEND_RESULT_UNKNOWN",
                "detail": detail,
            },
            True,
        )

    def _send_one(
        self,
        group: Group,
        group_name: str,
        run: dict,
        run_date: str,
        now: datetime,
        *,
        allow_hold: bool = False,
        allow_sent: bool = False,
    ) -> dict:
        """保留原注入点；发送 claim 和部分成功由发送阶段负责。"""
        return DeliveryStages(
            settings=self.settings,
            sender=self.sender,
            store=self.store,
            name_sync_audit=self._name_sync_audit,
            logger=logger,
        ).run(
            group,
            group_name,
            run,
            run_date,
            now,
            allow_hold=allow_hold,
            allow_sent=allow_sent,
        )

    def _finish_unknown_send(
        self,
        group_name: str,
        run_date: str,
        claim_id: str,
        stage: str,
        detail: str,
        *,
        submitted_at: str = "",
    ) -> dict:
        """保留原注入点；未知结果继续 fail-closed。"""
        return DeliveryStages(
            settings=self.settings,
            sender=self.sender,
            store=self.store,
            name_sync_audit=self._name_sync_audit,
            logger=logger,
        ).finish_unknown(
            group_name,
            run_date,
            claim_id,
            stage,
            detail,
            submitted_at=submitted_at,
        )

    # ================= 手动操作 =================

    def resolve_prompt_unknown(
        self,
        group_id: int,
        run_date: str,
        *,
        expected_operation_id: str,
    ) -> dict:
        """人工确认丢弃未知 Prompt 结果；本方法本身不调用任何外部模型。"""
        parsed_run_date = parse_date(run_date)
        if parsed_run_date is None:
            return {
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }
        group = self._get_group(group_id)
        if group is None:
            return {
                "status": "failed",
                "error_type": "GROUP_NOT_FOUND",
                "detail": f"群不存在 {group_id}",
            }
        run_date = parsed_run_date.isoformat()
        group_name = self._group_name(group)
        resolved, run, reason = self.store.resolve_prompt_result_unknown(
            group_name,
            run_date,
            expected_operation_id=expected_operation_id,
            now=datetime.now(ZoneInfo(self.settings.app_timezone)),
        )
        if not resolved:
            messages = {
                "state_corrupt": "运行状态损坏，禁止人工覆盖",
                "not_unknown": "当前任务已不是 Prompt 结果未知状态",
                "stale": "Prompt 未知状态已变化，请刷新后重新核对",
                "result_available": "已有可恢复 Prompt 结果，禁止丢弃",
            }
            return {
                "group_name": group_name,
                "status": "conflict",
                "error_type": "PROMPT_RESOLUTION_CONFLICT",
                "detail": messages.get(reason, "Prompt 未知状态无法消歧"),
                "reason": reason,
            }
        return {
            "group_name": group_name,
            "status": "resolved",
            "resolution": "discard_and_retry",
            "next_stage": "prompt",
            "updated_at": run.get("updated_at"),
            "detail": "已解除 Prompt 未知暂停；本次确认没有调用外部模型",
        }

    def resolve_send_unknown(
        self,
        group_id: int,
        run_date: str,
        *,
        resolution: str,
        expected_send_unknown_at: str,
    ) -> dict:
        """人工消歧文字提交检查点；不会调用微信 Sender。"""
        parsed_run_date = parse_date(run_date)
        if parsed_run_date is None:
            return {
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }
        group = self._get_group(group_id)
        if group is None:
            return {
                "status": "failed",
                "error_type": "GROUP_NOT_FOUND",
                "detail": f"群不存在 {group_id}",
            }
        run_date = parsed_run_date.isoformat()
        group_name = self._group_name(group)
        resolved, run, reason = self.store.resolve_text_send_unknown(
            group_name,
            run_date,
            resolution=resolution,
            expected_send_unknown_at=expected_send_unknown_at,
            now=datetime.now(ZoneInfo(self.settings.app_timezone)),
        )
        if not resolved:
            messages = {
                "state_corrupt": "运行状态损坏，禁止人工覆盖",
                "not_unknown": "当前任务已不是发送结果未知状态",
                "stale": "发送未知状态已变化，请刷新后重新核对",
                "unsupported_stage": "当前未知发生在图片阶段，此接口只处理文字提交",
                "invalid_resolution": "人工核对结论无效",
                "text_not_submitted": "没有文字提交动作记录，不能确认文字已发送",
            }
            return {
                "group_name": group_name,
                "status": "conflict",
                "error_type": "SEND_RESOLUTION_CONFLICT",
                "detail": messages.get(reason, "发送未知状态无法消歧"),
                "reason": reason,
            }
        return {
            "group_name": group_name,
            "status": "resolved",
            "resolution": resolution,
            "next_stage": "image" if resolution == "text_sent" and bool(group.image_enabled) else "text" if resolution == "not_sent" else "complete",
            "send_state": run.get("send_state"),
            "detail": "已记录人工核对结论；本次操作没有发送任何微信内容",
        }

    def reset_explicit_send_failure(
        self,
        group_id: int,
        run_date: str,
        *,
        expected_updated_at: str,
        expected_state_version: int,
    ) -> dict:
        """解除明确未提交的重试耗尽状态；不会调用微信 Sender。"""
        parsed_run_date = parse_date(run_date)
        if parsed_run_date is None:
            return {
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }
        group = self._get_group(group_id)
        if group is None:
            return {
                "status": "failed",
                "error_type": "GROUP_NOT_FOUND",
                "detail": f"群不存在 {group_id}",
            }
        run_date = parsed_run_date.isoformat()
        group_name = self._group_name(group)
        reset, run, reason = self.store.reset_explicit_send_failure(
            group_name,
            run_date,
            expected_updated_at=expected_updated_at,
            expected_state_version=expected_state_version,
            now=datetime.now(ZoneInfo(self.settings.app_timezone)),
        )
        if not reset:
            messages = {
                "state_corrupt": "运行状态损坏，禁止恢复发送",
                "stale": "任务状态已变化，请刷新后重新核对",
                "not_resolvable": "当前任务状态不能恢复发送",
                "not_explicit_failure": "当前任务不是明确未提交的重试耗尽状态",
                "active_claim": "当前任务仍有发送 claim，禁止并发恢复",
                "unresolved_attempt": "存在未决发送动作，必须人工核对结果",
                "submission_evidence": "已有提交、验证或发送证据，禁止自动重试",
            }
            return {
                "group_name": group_name,
                "status": "conflict",
                "error_type": "SEND_FAILURE_RESET_CONFLICT",
                "detail": messages.get(reason, "当前发送失败不能安全恢复"),
                "reason": reason,
            }
        return {
            "group_name": group_name,
            "status": "prepared",
            "send_state": run.get("send_state"),
            "run_status": run.get("status"),
            "updated_at": run.get("updated_at"),
            "state_version": run.get("state_version"),
            "detail": "已解除明确未提交的失败暂停；本次操作没有调用微信发送器",
        }

    def resolve_manual_send(
        self,
        group_id: int,
        run_date: str,
        *,
        resolution: str,
        expected_updated_at: str,
    ) -> dict:
        """人工核对整单发送状态；只更新 run.json，不调用微信 Sender。"""
        parsed_run_date = parse_date(run_date)
        if parsed_run_date is None:
            return {
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }
        group = self._get_group(group_id)
        if group is None:
            return {
                "status": "failed",
                "error_type": "GROUP_NOT_FOUND",
                "detail": f"群不存在 {group_id}",
            }
        run_date = parsed_run_date.isoformat()
        group_name = self._group_name(group)
        resolved, run, reason = self.store.resolve_manual_send(
            group_name,
            run_date,
            resolution=resolution,
            expected_updated_at=expected_updated_at,
            image_required=bool(group.image_enabled),
            now=datetime.now(ZoneInfo(self.settings.app_timezone)),
        )
        if not resolved:
            messages = {
                "state_corrupt": "运行状态损坏，禁止人工覆盖",
                "stale": "任务状态已变化，请刷新后重新核对",
                "not_resolvable": "当前任务状态不能进行人工发送核对",
                "not_held": "当前任务并未暂停待核对，请刷新后确认",
                "invalid_resolution": "人工核对结论无效",
                "ranking_missing": "排行榜文案不存在或为空，不能确认已发送",
                "image_missing": "日报图片不存在或为空，不能确认文字和图片均已发送",
            }
            return {
                "group_name": group_name,
                "status": "conflict",
                "error_type": "MANUAL_SEND_RESOLUTION_CONFLICT",
                "detail": messages.get(reason, "当前发送状态无法人工处理"),
                "reason": reason,
            }
        return {
            "group_name": group_name,
            "status": "resolved",
            "resolution": resolution,
            "next_stage": "complete" if resolution == "all_sent" else "image" if resolution == "text_sent" and bool(group.image_enabled) else "text",
            "send_state": run.get("send_state"),
            "run_status": run.get("status"),
            "updated_at": run.get("updated_at"),
            "detail": "已写入人工核对结论；本次操作没有调用微信发送器",
        }

    def force_generate(
        self,
        group_id: int,
        run_date: str | None = None,
        refresh_messages: bool = False,
        *,
        acquire_lock: bool = True,
    ) -> dict:
        if acquire_lock:
            with generation_mutex():
                return self.force_generate(
                    group_id,
                    run_date,
                    refresh_messages=refresh_messages,
                    acquire_lock=False,
                )
        if run_date is None:
            run_date = datetime.now(ZoneInfo(self.settings.app_timezone)).date().isoformat()
        else:
            parsed_run_date = parse_date(run_date)
            if parsed_run_date is None:
                return {
                    "status": "failed",
                    "error_type": "INVALID_RUN_DATE",
                    "error": "run_date 必须是有效的 YYYY-MM-DD 日期",
                }
            run_date = parsed_run_date.isoformat()
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "error": f"群不存在 {group_id}"}
        group_name = self._group_name(group)
        current = self.store.load_run(group_name, run_date)
        if current.get("status") == CORRUPT:
            return {
                "group_name": group_name,
                "status": "blocked",
                "error_type": RUN_STATE_CORRUPT,
                "detail": "运行状态文件损坏，需人工复核",
            }
        self._last_name_sync_report = self._sync_group_names_safe([group_id])
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "error": f"群不存在 {group_id}"}
        window = self.period_resolver.resolve(run_date=parse_date(run_date), timezone=self.settings.app_timezone)
        result = self._generate_one(
            group,
            window,
            run_date,
            force=True,
            refresh_messages=refresh_messages,
        )
        if not result.get("need_image"):
            return result
        job = self._make_image_job(group, run_date, force=True)
        return self._run_image_jobs([job], run_date)[0]

    def rebuild_prompt_from_snapshot(
        self,
        group_id: int,
        run_date: str,
        *,
        allow_topic_reselection: bool = False,
        acquire_lock: bool = True,
    ) -> dict:
        """只从当天 messages.json 重建排行榜和 Prompt，不取数、不生图。"""
        if acquire_lock:
            with generation_mutex():
                return self.rebuild_prompt_from_snapshot(
                    group_id,
                    run_date,
                    allow_topic_reselection=allow_topic_reselection,
                    acquire_lock=False,
                )

        parsed_run_date = parse_date(run_date)
        if parsed_run_date is None:
            return {
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }
        run_date = parsed_run_date.isoformat()
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "detail": f"群不存在 {group_id}"}

        group_name = self._group_name(group)
        snapshot_path = self.store.messages_path(group_name, run_date)
        if not snapshot_path.is_file():
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "MESSAGE_SNAPSHOT_MISSING",
                "detail": "当天 messages.json 不存在；已停止且不会临时重取微信消息",
            }

        current = self.store.load_run(group_name, run_date)
        if current.get("image_regen_status") in {"queued", "running"}:
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "IMAGE_REGEN_BUSY",
                "detail": "该运行正在生图，请完成后再重建 Prompt",
            }
        current_prompt_meta = (
            current.get("prompt_meta")
            if isinstance(current.get("prompt_meta"), dict)
            else {}
        )
        has_topic_selection = isinstance(current_prompt_meta.get("topic_selection"), dict)
        try:
            snapshot_messages = self._load_message_snapshot(snapshot_path)
            attribution = build_attribution_contract(snapshot_messages)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "MESSAGE_SNAPSHOT_INVALID",
                "detail": f"messages.json 无法建立说话人归属契约：{exc}",
            }
        stored_snapshot = str(
            current_prompt_meta.get("message_snapshot_sha256") or ""
        )
        stored_speakers = str(current_prompt_meta.get("speaker_fingerprint") or "")
        stored_selection = (
            current_prompt_meta.get("topic_selection")
            if isinstance(current_prompt_meta.get("topic_selection"), dict)
            else {}
        )
        attribution_matches = bool(
            stored_snapshot
            and stored_speakers
            and stored_snapshot == attribution.message_snapshot_sha256
            and stored_speakers == attribution.speaker_fingerprint
            and str(stored_selection.get("message_snapshot_sha256") or "")
            == attribution.message_snapshot_sha256
            and str(stored_selection.get("speaker_fingerprint") or "")
            == attribution.speaker_fingerprint
        )
        persisted_selection_valid = has_topic_selection and attribution_matches
        if not persisted_selection_valid and not allow_topic_reselection:
            current_hold_reason = str(current.get("send_hold_reason") or "")
            preserved_user_hold_reason = (
                current_hold_reason
                if current_hold_reason.startswith("USER_REQUEST_NO_SEND_")
                else ""
            )
            self.store.update(
                group_name,
                run_date,
                prompt_rebuild_status="required",
                prompt_rebuild_error="消息快照或说话人指纹与旧选题不一致",
                prompt_stale=True,
                image_stale=True,
                artifact_stale_reason="TOPIC_SELECTION_SNAPSHOT_INVALID",
                send_hold=True,
                send_hold_reason=(
                    preserved_user_hold_reason
                    or "TOPIC_SELECTION_SNAPSHOT_INVALID"
                ),
                needs_manual_send=True,
            )
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "TOPIC_SELECTION_SNAPSHOT_INVALID",
                "detail": "旧选题缺少匹配的消息快照/说话人指纹；已停止且不会隐式重新选题",
            }
        reselect_topics = not persisted_selection_valid

        keep_sent = current.get("status") == SENT
        current_hold_reason = str(current.get("send_hold_reason") or "")
        preserved_user_hold_reason = (
            current_hold_reason
            if current_hold_reason.startswith("USER_REQUEST_NO_SEND_")
            else ""
        )
        self.store.update(
            group_name,
            run_date,
            prompt_rebuild_status="running",
            prompt_rebuild_error="",
            send_hold=True,
            send_hold_reason=preserved_user_hold_reason or "PROMPT_REBUILDING",
            needs_manual_send=True,
        )
        window = self.period_resolver.resolve(
            run_date=parsed_run_date,
            timezone=self.settings.app_timezone,
        )
        result = self._generate_one(
            group,
            window,
            run_date,
            force=True,
            refresh_messages=False,
            reuse_persisted_topic_selection=not reselect_topics,
        )
        rebuilt = self.store.load_run(group_name, run_date)
        if result.get("status") == "failed" or bool(rebuilt.get("image_force_local_fallback")):
            detail = str(
                result.get("detail")
                or result.get("error")
                or rebuilt.get("prompt_original_error")
                or "重建失败"
            )[:500]
            self.store.update(
                group_name,
                run_date,
                status=SENT if keep_sent else FAILED,
                prompt_rebuild_status="failed",
                prompt_rebuild_error=detail,
                prompt_topic_reselected=reselect_topics,
                send_hold=True,
                needs_manual_send=True,
            )
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": str(result.get("error_type") or PROMPT_FAILED),
                "detail": detail,
            }

        self.store.update(
            group_name,
            run_date,
            status=SENT if keep_sent else PROMPT_READY,
            prompt_rebuild_status="ready_for_review",
            prompt_rebuild_error="",
            image_regen_status="prompt_rebuilt",
            image_regen_error="",
            prompt_topic_reselected=reselect_topics,
            send_hold=True,
            send_hold_reason=(
                preserved_user_hold_reason or "PROMPT_REBUILT_REVIEW_REQUIRED"
            ),
            needs_manual_send=True,
        )
        return {
            "group_name": group_name,
            "status": "prompt_ready",
            "detail": (
                "已从保存的 messages.json 显式重新选题并重建 Prompt；未取数，未生图"
                if reselect_topics
                else "已复用 run.json 中已校验选题和既定分镜重建 Prompt；未取数，未生图"
            ),
        }

    def rebuild_prompts_from_snapshots(
        self,
        targets: list[tuple[int, str, str]],
        *,
        acquire_lock: bool = True,
    ) -> list[dict]:
        """按稳定群 ID 并行重建已有快照；整个批次不访问微信或生图。"""
        if acquire_lock:
            with generation_mutex():
                return self.rebuild_prompts_from_snapshots(
                    targets,
                    acquire_lock=False,
                )
        if not targets:
            return []

        seen: set[tuple[int, str]] = set()
        normalized: list[tuple[int, str, str]] = []
        for group_id, wechat_group_id, run_date in targets:
            key = (int(group_id), str(run_date))
            if key in seen:
                continue
            seen.add(key)
            normalized.append((int(group_id), str(wechat_group_id), str(run_date)))

        def rebuild_one(target: tuple[int, str, str]) -> dict:
            group_id, expected_wechat_group_id, run_date = target
            group = self._get_group(group_id)
            if group is None:
                return {
                    "group_id": group_id,
                    "status": "failed",
                    "error_type": "GROUP_NOT_FOUND",
                    "detail": f"群不存在 {group_id}",
                }
            actual_wechat_group_id = str(group.wechat_group_id or "").strip()
            if not expected_wechat_group_id or actual_wechat_group_id != expected_wechat_group_id.strip():
                return {
                    "group_id": group_id,
                    "status": "failed",
                    "error_type": "GROUP_IDENTITY_MISMATCH",
                    "detail": "group_id 与 wechat_group_id 不匹配，已停止重建",
                }
            group_name = self._group_name(group)
            current = self.store.load_run(group_name, run_date)
            try:
                run_group_id = int(current.get("group_id"))
            except (TypeError, ValueError):
                run_group_id = -1
            run_wechat_group_id = str(current.get("wechat_group_id") or "").strip()
            if run_group_id != group_id or (
                run_wechat_group_id and run_wechat_group_id != actual_wechat_group_id
            ):
                return {
                    "group_id": group_id,
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": "RUN_IDENTITY_MISMATCH",
                    "detail": "run.json 群身份与目标不一致，已停止重建",
                }
            result = self.rebuild_prompt_from_snapshot(
                group_id,
                run_date,
                acquire_lock=False,
            )
            result.setdefault("group_id", group_id)
            result.setdefault("wechat_group_id", actual_wechat_group_id)
            return result

        # 快照重建本身只读本地文件，允许六个群同时进入工作池；真正的
        # Codex 文本调用仍由 provider 侧的 2 路信号量限流。
        limit = min(len(normalized), 6)
        if limit <= 1:
            return [rebuild_one(target) for target in normalized]
        results: dict[int, dict] = {}
        with ThreadPoolExecutor(
            max_workers=limit,
            thread_name_prefix="groupbrief-prompt-rebuild",
        ) as executor:
            futures = {
                executor.submit(rebuild_one, target): index
                for index, target in enumerate(normalized)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    results[index] = {
                        "group_id": normalized[index][0],
                        "status": "failed",
                        "error_type": "PROMPT_REBUILD_FAILED",
                        "detail": str(exc)[:500],
                    }
        return [results[index] for index in range(len(normalized))]

    def force_send(
        self,
        group_id: int,
        run_date: str | None = None,
        *,
        confirm_regenerated: bool = False,
        confirm_late_send: bool = False,
    ) -> dict:
        now = datetime.now(ZoneInfo(self.settings.app_timezone))
        if run_date is None:
            run_date = now.date().isoformat()
        else:
            parsed_run_date = parse_date(run_date)
            if parsed_run_date is None:
                return {
                    "status": "failed",
                    "error_type": "INVALID_RUN_DATE",
                    "error": "run_date 必须是有效的 YYYY-MM-DD 日期",
                }
            run_date = parsed_run_date.isoformat()
        self._last_name_sync_report = self._sync_group_names_safe([group_id])
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "error": f"群不存在 {group_id}"}
        group_name = group.display_name or group.wechat_group_name
        run = self.store.load_run(group_name, run_date)
        can_resend_review = (
            run.get("status") == SENT
            and run.get("image_regen_status") == "ready_for_review"
            and confirm_regenerated
        )
        if run.get("status") not in (IMAGE_READY, READY_TO_SEND) and not can_resend_review:
            return {"status": "failed", "error": f"状态 {run.get('status')} 不可发送"}
        scheduled_date = parse_date(run_date)
        send_clock = parse_send_time(group.send_time or run.get("send_time", "08:30"))
        scheduled_at = datetime.combine(scheduled_date, send_clock, tzinfo=now.tzinfo)
        late_cutoff = scheduled_at + timedelta(
            minutes=max(int(self.settings.wechat_late_send_window_minutes), 0)
        )
        is_late = now > late_cutoff
        if is_late and not confirm_late_send:
            self.store.update(
                group_name,
                run_date,
                send_state="held",
                send_hold=True,
                send_hold_reason="MISSED_SEND_WINDOW",
                needs_manual_send=True,
                send_error="已超过自动补发窗口，需显式确认逾期发送",
                send_error_type="MISSED_SEND_WINDOW",
            )
            return {
                "status": "failed",
                "error_type": "MISSED_SEND_WINDOW",
                "error": "已超过自动补发窗口，设置 confirm_late_send 后才能发送",
            }
        if run.get("send_hold"):
            hold_reason = run.get("send_hold_reason")
            if hold_reason == "SEND_RESULT_UNKNOWN" or run.get("send_state") == "unknown":
                return {"status": "failed", "error": "上次发送结果未知，必须人工核对后处理，禁止自动重复发送"}
            if hold_reason == "MISSED_SEND_WINDOW":
                if not confirm_late_send:
                    return {"status": "failed", "error": "已超过自动补发窗口，显式确认后才能发送"}
            elif run.get("image_regen_status") != "ready_for_review":
                return {"status": "failed", "error": "Prompt 已修改但新图尚未完成审核，不能发送"}
            elif not confirm_regenerated:
                return {"status": "failed", "error": "重新生成的图片正在人工审核，显式确认后才能发送"}
        allow_hold = bool(
            (run.get("send_hold_reason") == "MISSED_SEND_WINDOW" and confirm_late_send)
            or (run.get("image_regen_status") == "ready_for_review" and confirm_regenerated)
        )
        return self._send_one(
            group,
            group_name,
            run,
            run_date,
            now,
            allow_hold=allow_hold,
            allow_sent=can_resend_review,
        )

    # ================= 工具 =================

    def _sync_group_names(self, group_ids: list[int] | None = None) -> GroupNameSyncReport:
        from sqlmodel import Session

        def operation() -> GroupNameSyncReport:
            with Session(repo.engine) as session:
                return GroupNameSyncService(self.data_source).sync(session, group_ids=group_ids)

        report = run_with_sqlite_retry(
            operation,
            max_attempts=self.settings.sqlite_retry_max_attempts,
        )
        logger.info(
            "流水线群名同步：status=%s checked=%d updated=%d skipped=%d",
            report.status,
            report.checked,
            len(report.updated),
            len(report.skipped),
        )
        return report

    def _sync_group_names_safe(
        self,
        group_ids: list[int] | None = None,
    ) -> GroupNameSyncReport:
        """实时群名同步失败时保留缓存名称，不让全批生成/发送消失。"""
        try:
            return self._sync_group_names(group_ids)
        except Exception as exc:
            logger.exception("流水线群名同步异常，降级使用数据库缓存名称")
            return GroupNameSyncReport(
                status="unavailable",
                source=str(getattr(self.data_source, "name", "unknown") or "unknown"),
                checked=len(group_ids or []),
                detail=f"群名同步异常：{type(exc).__name__}",
            )

    def _name_sync_audit(self, group: Group) -> dict:
        mode = send_target_mode(group)
        report = self._last_name_sync_report
        if mode == "manual":
            status = "manual_override"
        elif report is not None and report.is_fresh(group.id):
            status = "fresh"
        else:
            status = "cached"
        return {
            "name_sync_status": status,
            "name_sync_at": report.synced_at if report is not None else "",
        }

    def _due_sync_group_ids(
        self,
        groups: list[Group],
        run_date: str,
        now: datetime,
        *,
        recovery: bool = False,
    ) -> list[int]:
        late_window = timedelta(minutes=max(int(self.settings.wechat_late_send_window_minutes), 0))
        group_ids: list[int] = []
        for group in groups:
            if group.id is None or not bool(getattr(group, "wechat_send_enabled", False)):
                continue
            group_name = group.display_name or group.wechat_group_name
            run = self.store.load_run(group_name, run_date)
            if run.get("status") not in (IMAGE_READY, READY_TO_SEND):
                continue
            if run.get("sent_at") or run.get("send_hold"):
                continue
            send_time = parse_send_time(group.send_time or run.get("send_time", "08:30"))
            due_at = datetime.combine(date.fromisoformat(run_date), send_time, tzinfo=now.tzinfo)
            if due_at <= now and (recovery or now <= due_at + late_window):
                group_ids.append(int(group.id))
        return group_ids

    def _load_groups(self, group_ids: list[int] | None = None) -> list[Group]:
        from sqlmodel import Session

        def operation() -> list[Group]:
            with Session(repo.engine) as session:
                return repo.list_groups(session, only_enabled=True)

        groups = run_with_sqlite_retry(
            operation,
            max_attempts=self.settings.sqlite_retry_max_attempts,
        )
        if group_ids:
            groups = [g for g in groups if g.id in group_ids]
        return groups

    def _get_group(self, group_id: int) -> Group | None:
        from sqlmodel import Session

        def operation() -> Group | None:
            with Session(repo.engine) as session:
                return repo.get_active_group(session, group_id)

        return run_with_sqlite_retry(
            operation,
            max_attempts=self.settings.sqlite_retry_max_attempts,
        )

    def _save_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _load_message_snapshot(path: Path) -> list[V2Message]:
        """读取已落盘的日报消息；损坏时失败，不静默回源。"""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError("messages.json 必须是非空数组")

        messages: list[V2Message] = []
        seen_message_ids: set[str] = set()
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index} 条消息不是对象")
            timestamp_text = str(item.get("timestamp") or "").strip()
            try:
                timestamp = datetime.fromisoformat(timestamp_text)
            except ValueError as exc:
                raise ValueError(f"第 {index} 条消息时间无效") from exc
            message_id = str(item.get("message_id") or "").strip()
            group_id = str(item.get("group_id") or "").strip()
            if not message_id or not group_id:
                raise ValueError(f"第 {index} 条消息缺少 message_id/group_id")
            if message_id in seen_message_ids:
                raise ValueError(f"第 {index} 条消息的 message_id 重复：{message_id}")
            seen_message_ids.add(message_id)
            messages.append(
                V2Message(
                    message_id=message_id,
                    group_id=group_id,
                    group_name=str(item.get("group_name") or ""),
                    sender_id=str(item.get("sender_id") or ""),
                    sender_name=str(item.get("sender_name") or ""),
                    timestamp=timestamp,
                    message_type=str(item.get("message_type") or "text"),
                    content=str(item.get("content") or ""),
                    upstream_sender_name=str(item.get("upstream_sender_name") or ""),
                    sender_name_source=str(
                        item.get("sender_name_source")
                        if "sender_name_source" in item
                        else "snapshot"
                    ),
                )
            )
        return messages


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        validate_run_date(value)
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_send_time(value: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return time(8, 30)
