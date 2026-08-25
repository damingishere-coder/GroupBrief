"""DailyPipeline 的单群生成阶段实现。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from app.ai.concurrency import bounded_slot
from app.ai.prompt_builder import GroupSummaryImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.config.settings import Settings
from app.data_sources.base import V2Message, WeChatDataSource
from app.db.models import Group
from app.pipeline.stage_result import StageResult
from app.providers.ai.base import ExternalCallResultUnknownError
from app.ranking.engine import RankingEngine
from app.ranking.engine_types import RankingResult
from app.ranking.renderer import RankingRenderer
from app.scheduler.period import PeriodWindow
from app.services.group_name_sync import effective_send_target, send_target_mode
from app.v2.constants import (
    DATA_READY,
    FAILED,
    IMAGE_READY,
    MESSAGE_FETCH_FAILED,
    MESSAGE_SNAPSHOT_INVALID,
    PENDING,
    PROMPT_FAILED,
    PROMPT_READY,
    RANKING_FAILED,
    RANKING_READY,
    READY_TO_SEND,
    RUN_STATE_CORRUPT,
    SENT,
    WECHAT_DATA_UNAVAILABLE,
)
from app.v2.run_store import RunStore


@dataclass
class GenerationContext:
    group: Group
    window: PeriodWindow
    run_date: str
    force: bool
    refresh_messages: bool
    reuse_persisted_topic_selection: bool
    group_name: str
    run: dict
    persisted_prompt_meta: dict
    period_start: str
    period_end: str
    started_at: float = field(default_factory=perf_counter)
    timings: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PromptStageOutput:
    prompt_meta: dict | None


class GenerationStages:
    """按快照、排行、Prompt、图片决策顺序执行单群生成。"""

    def __init__(
        self,
        *,
        settings: Settings,
        data_source: WeChatDataSource,
        ranking_engine: RankingEngine,
        renderer: RankingRenderer,
        prompt_builder: GroupSummaryImagePromptBuilder,
        store: RunStore,
        group_name: Callable[[Group], str],
        name_sync_audit: Callable[[Group], dict],
        get_group: Callable[[int], Group | None],
        prompt_operation_hash: Callable[[PromptInput], str],
        save_json: Callable[[Path, Any], None],
        load_message_snapshot: Callable[[Path], list[V2Message]],
        logger,
    ) -> None:
        self.settings = settings
        self.data_source = data_source
        self.ranking_engine = ranking_engine
        self.renderer = renderer
        self.prompt_builder = prompt_builder
        self.store = store
        self._group_name = group_name
        self._name_sync_audit = name_sync_audit
        self._get_group = get_group
        self._prompt_operation_hash = prompt_operation_hash
        self._save_json = save_json
        self._load_message_snapshot = load_message_snapshot
        self.logger = logger

    def run(
        self,
        group: Group,
        window: PeriodWindow,
        run_date: str,
        force: bool,
        *,
        refresh_messages: bool = False,
        reuse_persisted_topic_selection: bool = False,
    ) -> dict:
        context = self._context(
            group,
            window,
            run_date,
            force,
            refresh_messages,
            reuse_persisted_topic_selection,
        )

        prepared = self._prepare_run(context)
        if prepared.is_terminal:
            return self._finish(context, prepared.terminal_response())

        message_stage = self._load_or_fetch_messages(context)
        if message_stage.is_terminal:
            return self._finish(context, message_stage.terminal_response())
        messages = message_stage.next_value()

        if context.refresh_messages:
            refreshed = self._refresh_snapshot_and_ranking(context, messages)
            return self._finish(context, refreshed.terminal_response())

        ranking_stage = self._build_ranking(context, messages)
        if ranking_stage.is_terminal:
            return self._finish(context, ranking_stage.terminal_response())

        prompt_stage = self._build_prompt(
            context,
            messages,
            ranking_stage.next_value(),
        )
        if prompt_stage.is_terminal:
            return self._finish(context, prompt_stage.terminal_response())
        prompt_meta = prompt_stage.next_value().prompt_meta
        if isinstance(prompt_meta, dict):
            self.store.update(
                context.group_name,
                context.run_date,
                prompt_meta=prompt_meta,
            )
        image_stage = self._decide_image(context)
        return self._finish(context, image_stage.terminal_response())

    def _context(
        self,
        group: Group,
        window: PeriodWindow,
        run_date: str,
        force: bool,
        refresh_messages: bool,
        reuse_persisted_topic_selection: bool,
    ) -> GenerationContext:
        started_at = perf_counter()
        group_name = self._group_name(group)
        run = self.store.load_run(group_name, run_date)
        prompt_meta = run.get("prompt_meta")
        persisted_prompt_meta = prompt_meta if isinstance(prompt_meta, dict) else {}
        return GenerationContext(
            group=group,
            window=window,
            run_date=run_date,
            force=force,
            refresh_messages=refresh_messages,
            reuse_persisted_topic_selection=reuse_persisted_topic_selection,
            group_name=group_name,
            run=run,
            persisted_prompt_meta=persisted_prompt_meta,
            period_start=window.period_start_str(),
            period_end=window.period_end_str(),
            started_at=started_at,
        )

    def _prepare_run(
        self,
        context: GenerationContext,
    ) -> StageResult[GenerationContext]:
        run = context.run
        if (
            not context.force
            and not context.refresh_messages
            and run.get("status") in (IMAGE_READY, READY_TO_SEND, SENT)
        ):
            self.logger.info(
                "群 %s %s 已到 %s，跳过生成",
                context.group_name,
                context.run_date,
                run.get("status"),
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "skipped",
                    "detail": f"已{run.get('status')}",
                }
            )

        group = context.group
        base = {
            "group_id": str(group.id),
            "wechat_group_id": group.wechat_group_id,
            "wechat_group_name": group.wechat_group_name,
            "effective_send_target": effective_send_target(group),
            "send_target_mode": send_target_mode(group),
            **self._name_sync_audit(group),
            "period_start": context.period_start,
            "period_end": context.period_end,
            "send_time": group.send_time,
            "image_enabled": bool(group.image_enabled),
            "ranking_template": group.ranking_template,
            "image_prompt_template": group.image_prompt_template,
            "image_theme": group.image_theme,
            "image_theme_custom": group.image_theme_custom,
            "wechat_send_enabled": bool(getattr(group, "wechat_send_enabled", False)),
            "provider": self.data_source.name,
            "failed_stage": None,
            "error": None,
        }
        self.store.update(context.group_name, context.run_date, status=PENDING, **base)
        return StageResult.proceed(context)

    def _load_or_fetch_messages(
        self,
        context: GenerationContext,
    ) -> StageResult[list[V2Message]]:
        group = context.group
        if not group.wechat_group_id:
            self.store.update(
                context.group_name,
                context.run_date,
                status=FAILED,
                failed_stage="data",
                error="群未绑定微信群 ID",
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": WECHAT_DATA_UNAVAILABLE,
                }
            )

        started_at = perf_counter()
        snapshot_path = self.store.messages_path(
            context.group_name,
            context.run_date,
        )
        if snapshot_path.is_file() and not context.refresh_messages:
            return self._reuse_snapshot(context, snapshot_path, started_at)
        return self._fetch_messages(context, snapshot_path, started_at)

    def _reuse_snapshot(
        self,
        context: GenerationContext,
        snapshot_path: Path,
        started_at: float,
    ) -> StageResult[list[V2Message]]:
        try:
            messages = self._load_message_snapshot(snapshot_path)
        except (OSError, UnicodeError, ValueError) as exc:
            context.timings["fetch_ms"] = round((perf_counter() - started_at) * 1000)
            detail = f"当天消息快照无法读取，已停止且不会隐式重抓：{str(exc)[:220]}"
            self.store.update(
                context.group_name,
                context.run_date,
                status=FAILED,
                failed_stage="data",
                error=detail,
                error_type=MESSAGE_SNAPSHOT_INVALID,
                message_snapshot_reused=False,
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": MESSAGE_SNAPSHOT_INVALID,
                    "detail": detail,
                }
            )

        context.timings["fetch_ms"] = round((perf_counter() - started_at) * 1000)
        self.store.update(
            context.group_name,
            context.run_date,
            status=DATA_READY,
            message_count=len(messages),
            message_snapshot_reused=True,
            message_snapshot_refreshed=False,
            message_snapshot_path=snapshot_path.name,
        )
        self.logger.info(
            "群 %s 复用当天消息快照：%s（%d 条）",
            context.group_name,
            snapshot_path,
            len(messages),
        )
        return StageResult.proceed(messages)

    def _fetch_messages(
        self,
        context: GenerationContext,
        snapshot_path: Path,
        started_at: float,
    ) -> StageResult[list[V2Message]]:
        group = context.group
        try:
            with bounded_slot("wechat_fetch", self.settings.wechat_fetch_concurrency):
                fetch = self.data_source.fetch_messages(
                    group.wechat_group_id,
                    context.window.period_start,
                    context.window.period_end,
                )
        except Exception as exc:
            context.timings["fetch_ms"] = round((perf_counter() - started_at) * 1000)
            self.store.update(
                context.group_name,
                context.run_date,
                status=FAILED,
                failed_stage="data",
                error=str(exc)[:300],
                error_type=MESSAGE_FETCH_FAILED,
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": MESSAGE_FETCH_FAILED,
                    "detail": str(exc)[:300],
                }
            )

        context.timings["fetch_ms"] = round((perf_counter() - started_at) * 1000)
        fetch_metrics = fetch.meta if isinstance(getattr(fetch, "meta", None), dict) else {}
        if fetch_metrics:
            self.store.update(
                context.group_name,
                context.run_date,
                fetch_metrics=fetch_metrics,
            )
        if fetch.status.value != "OK" or not fetch.messages:
            error_type = fetch.error_type or MESSAGE_FETCH_FAILED
            self.store.update(
                context.group_name,
                context.run_date,
                status=FAILED,
                failed_stage="data",
                error=fetch.detail or fetch.status.value,
                error_type=error_type,
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": error_type,
                    "detail": fetch.detail,
                }
            )

        messages = list(fetch.messages)
        if not context.refresh_messages:
            self._save_json(
                snapshot_path,
                [message.to_dict() for message in messages],
            )
            self.store.update(
                context.group_name,
                context.run_date,
                status=DATA_READY,
                message_count=len(messages),
                message_snapshot_reused=False,
                message_snapshot_refreshed=False,
                message_snapshot_saved_at=datetime.now().astimezone().isoformat(),
                message_snapshot_path=snapshot_path.name,
                message_snapshot_period_start=context.period_start,
                message_snapshot_period_end=context.period_end,
            )
        return StageResult.proceed(messages)

    def _refresh_snapshot_and_ranking(
        self,
        context: GenerationContext,
        messages: list[V2Message],
    ) -> StageResult[dict]:
        started_at = perf_counter()
        try:
            ranking = self.ranking_engine.compute(
                messages,
                context.group_name,
                context.period_start,
                context.period_end,
                top_limit=10,
            )
            ranking_txt = self.renderer.render(
                ranking,
                template_name=context.group.ranking_template,
            )
        except Exception as exc:
            context.timings["ranking_ms"] = round((perf_counter() - started_at) * 1000)
            self.store.update(
                context.group_name,
                context.run_date,
                status=context.run.get("status") or PENDING,
                failed_stage=context.run.get("failed_stage"),
                error=context.run.get("error"),
                message_refresh_status="failed",
                message_refresh_error=str(exc)[:300],
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": RANKING_FAILED,
                    "detail": "新消息已读取，但排行榜计算失败，旧快照未被替换",
                }
            )

        context.timings["ranking_ms"] = round((perf_counter() - started_at) * 1000)
        snapshot_path = self.store.messages_path(context.group_name, context.run_date)
        self._save_json(snapshot_path, [message.to_dict() for message in messages])
        self._save_json(
            self.store.ranking_json_path(context.group_name, context.run_date),
            ranking.to_dict(),
        )
        self.store.ranking_txt_path(context.group_name, context.run_date).write_text(
            ranking_txt,
            encoding="utf-8",
        )
        next_status = SENT if context.run.get("status") == SENT else RANKING_READY
        self.store.update(
            context.group_name,
            context.run_date,
            status=next_status,
            failed_stage=None,
            error=None,
            speaker_count=ranking.speaker_count,
            message_count=ranking.message_count,
            message_snapshot_reused=False,
            message_snapshot_refreshed=True,
            message_snapshot_saved_at=datetime.now().astimezone().isoformat(),
            message_snapshot_path=snapshot_path.name,
            message_snapshot_period_start=context.period_start,
            message_snapshot_period_end=context.period_end,
            message_refresh_status="completed",
            message_refresh_error="",
            prompt_rebuild_status="required",
            prompt_rebuild_error="",
            send_hold=True,
            send_hold_reason="MESSAGE_SNAPSHOT_REFRESHED",
            needs_manual_send=True,
        )
        return StageResult.stop(
            {
                "group_name": context.group_name,
                "status": "data_ready",
                "detail": "当天消息快照和排行榜已更新；未重建 Prompt，未生图",
            }
        )

    def _build_ranking(
        self,
        context: GenerationContext,
        messages: list[V2Message],
    ) -> StageResult[RankingResult]:
        started_at = perf_counter()
        try:
            ranking = self.ranking_engine.compute(
                messages,
                context.group_name,
                context.period_start,
                context.period_end,
                top_limit=10,
            )
        except Exception as exc:
            self.store.update(
                context.group_name,
                context.run_date,
                status=FAILED,
                failed_stage="ranking",
                error=str(exc)[:300],
            )
            context.timings["ranking_ms"] = round((perf_counter() - started_at) * 1000)
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": RANKING_FAILED,
                }
            )

        context.timings["ranking_ms"] = round((perf_counter() - started_at) * 1000)
        self._save_json(
            self.store.ranking_json_path(context.group_name, context.run_date),
            ranking.to_dict(),
        )
        ranking_txt = self.renderer.render(
            ranking,
            template_name=context.group.ranking_template,
        )
        self.store.ranking_txt_path(context.group_name, context.run_date).write_text(
            ranking_txt,
            encoding="utf-8",
        )
        self.store.update(
            context.group_name,
            context.run_date,
            status=RANKING_READY,
            speaker_count=ranking.speaker_count,
            message_count=ranking.message_count,
        )
        return StageResult.proceed(ranking)

    def _build_prompt(
        self,
        context: GenerationContext,
        messages: list[V2Message],
        ranking: RankingResult,
    ) -> StageResult[PromptStageOutput]:
        group = context.group
        prompt_messages = [
            message for message in messages if RankingEngine._countable(message)
        ]
        prompt_input = PromptInput(
            group_name=context.group_name,
            visible_group_name=str(
                context.run.get("wechat_group_name")
                or group.wechat_group_name
                or context.group_name
            ).strip(),
            group_id=str(group.id or group.wechat_group_id or context.group_name),
            run_date=context.run_date,
            period_start=context.period_start,
            period_end=context.period_end,
            report_date=context.window.period_end.date().isoformat(),
            message_count=ranking.message_count,
            speaker_count=ranking.speaker_count,
            messages=prompt_messages,
            template=group.image_prompt_template,
            image_theme=group.image_theme,
            image_theme_custom=group.image_theme_custom,
            template_override=getattr(group, "image_prompt_override", "") or "",
            previous_theme_signature=self.store.previous_theme_signature(
                context.group_name,
                context.run_date,
            ),
            persisted_theme_meta=context.persisted_prompt_meta or None,
            persisted_topic_selection=(
                context.persisted_prompt_meta.get("topic_selection")
                if context.reuse_persisted_topic_selection
                and isinstance(context.persisted_prompt_meta.get("topic_selection"), dict)
                else None
            ),
            recent_layout_history=self.store.recent_layout_history(
                context.group_name,
                context.run_date,
                limit=3,
            ),
        )
        return self._execute_prompt_operation(context, prompt_input)

    def _execute_prompt_operation(
        self,
        context: GenerationContext,
        prompt_input: PromptInput,
    ) -> StageResult[PromptStageOutput]:
        input_hash = self._prompt_operation_hash(prompt_input)
        operation_id, operation_state, claim_reason = self.store.claim_prompt_operation(
            context.group_name,
            context.run_date,
            input_hash=input_hash,
            force=context.force,
        )
        if claim_reason == "state_corrupt":
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "blocked",
                    "error_type": RUN_STATE_CORRUPT,
                    "detail": "运行状态文件损坏，已阻止 AI 调用",
                }
            )
        if claim_reason == "result_unknown":
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "held",
                    "error_type": "PROMPT_RESULT_UNKNOWN",
                    "detail": "上次 AI 调用结果未知，需人工核对后才能再次生成",
                }
            )

        started_at = perf_counter()
        if claim_reason == "result_recorded":
            operation_id = str(operation_state.get("prompt_operation_id") or "")
            committed = self.store.commit_recorded_prompt(
                context.group_name,
                context.run_date,
                operation_id,
            )
            prompt_meta = committed.get("prompt_meta")
        elif claim_reason == "already_completed":
            prompt_meta = operation_state.get("prompt_meta")
            self.store.update(context.group_name, context.run_date, status=PROMPT_READY)
        else:
            if not operation_id:
                raise RuntimeError(f"无法领取 Prompt 操作：{claim_reason}")
            try:
                prompt_out = self.prompt_builder.build(prompt_input)
            except ExternalCallResultUnknownError as exc:
                self.store.mark_prompt_result_unknown(
                    context.group_name,
                    context.run_date,
                    operation_id,
                    error=str(exc),
                )
                self._record_prompt_timing(context, started_at)
                self.store.update(
                    context.group_name,
                    context.run_date,
                    failed_stage="prompt",
                    error=str(exc)[:300],
                    error_type="PROMPT_RESULT_UNKNOWN",
                )
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "held",
                        "error_type": "PROMPT_RESULT_UNKNOWN",
                        "detail": str(exc)[:300],
                    }
                )
            if not prompt_out.success:
                self.store.fail_prompt_operation(
                    context.group_name,
                    context.run_date,
                    operation_id,
                    error=prompt_out.error,
                )
                self._record_prompt_timing(context, started_at)
                self.store.update(
                    context.group_name,
                    context.run_date,
                    status=FAILED,
                    failed_stage="prompt",
                    error=prompt_out.error,
                    error_type=PROMPT_FAILED,
                )
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "failed",
                        "error_type": PROMPT_FAILED,
                        "detail": prompt_out.error,
                    }
                )
            self.store.record_prompt_result(
                context.group_name,
                context.run_date,
                operation_id,
                prompt=prompt_out.prompt,
                meta=prompt_out.meta,
            )
            committed = self.store.commit_recorded_prompt(
                context.group_name,
                context.run_date,
                operation_id,
            )
            prompt_meta = committed.get("prompt_meta")

        self._record_prompt_timing(context, started_at)
        return StageResult.proceed(
            PromptStageOutput(
                prompt_meta=prompt_meta if isinstance(prompt_meta, dict) else None
            )
        )

    def _decide_image(
        self,
        context: GenerationContext,
    ) -> StageResult[dict]:
        group = context.group
        current_group = group
        if group.id is not None:
            try:
                refreshed_group = self._get_group(group.id)
            except Exception as exc:
                self.logger.warning(
                    "群 %s 生图开关刷新失败，沿用本次读取的配置：%s",
                    context.group_name,
                    exc,
                )
            else:
                if refreshed_group is not None:
                    current_group = refreshed_group

        current_image_enabled = bool(current_group.image_enabled)
        group.image_enabled = current_image_enabled
        self.store.update(
            context.group_name,
            context.run_date,
            image_enabled=current_image_enabled,
        )
        if not current_image_enabled:
            self.store.update(context.group_name, context.run_date, status=READY_TO_SEND)
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "ready_to_send",
                    "detail": "未启用生图",
                }
            )
        return StageResult.stop(
            {
                "group_name": context.group_name,
                "status": "prompt_ready",
                "need_image": True,
            }
        )

    @staticmethod
    def _record_prompt_timing(context: GenerationContext, started_at: float) -> None:
        context.timings["summary_ms"] = round((perf_counter() - started_at) * 1000)
        context.timings["deepseek_ms"] = context.timings["summary_ms"]

    def _finish(self, context: GenerationContext, result: dict) -> dict:
        timings = context.timings
        timings["total_ms"] = round((perf_counter() - context.started_at) * 1000)
        current = self.store.load_run(context.group_name, context.run_date)
        prompt_meta = current.get("prompt_meta")
        meta = prompt_meta if isinstance(prompt_meta, dict) else {}
        timings["summary_calls"] = int(meta.get("api_call_count") or 0)
        timings["deepseek_calls"] = timings["summary_calls"]
        timings["chunk_count"] = int(meta.get("chunk_count") or 0)
        self.store.update(
            context.group_name,
            context.run_date,
            stage_timings=timings,
        )
        self.logger.info(
            "群生成耗时 group=%s fetch_ms=%d ranking_ms=%d summary_ms=%d "
            "summary_calls=%d chunks=%d total_ms=%d status=%s",
            context.group_name,
            timings.get("fetch_ms", 0),
            timings.get("ranking_ms", 0),
            timings.get("summary_ms", timings.get("deepseek_ms", 0)),
            timings["summary_calls"],
            timings["chunk_count"],
            timings["total_ms"],
            result.get("status", ""),
        )
        return result
