"""V2 每日全流程流水线（P7）。

生成阶段（默认 00:15，run_date 决定周期）：
    PENDING → 首次取数/复用当天快照(messages.json) → DATA_READY → 排行(ranking.json/txt)
    → RANKING_READY → Codex GPT / DeepSeek 备用(image_prompt.txt) → PROMPT_READY
    → Codex 串行生图(daily_image.png) → IMAGE_READY → READY_TO_SEND
发送阶段（每群 send_time）：
    READY_TO_SEND/IMAGE_READY → 发排行榜文字 → 发图片 → SENT

约束：
- 每个群独立状态；某群失败不阻塞其他群；
- 生图阶段使用全局单队列严格串行；
- 同一群同一统计周期已到终态则跳过（force 可重跑）；
- 同一日报日期的消息默认只读取一次；显式 refresh_messages 只覆盖当天快照，不连带重建 Prompt 或生图；
- SENT 绝不重复发送（force_send 允许重发内容）。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import perf_counter
from zoneinfo import ZoneInfo

from app.ai.concurrency import bounded_slot, normalized_limit
from app.ai.prompt_builder import GroupSummaryImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.data_sources.base import V2Message, WeChatDataSource
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.db import repository as repo
from app.db.models import Group
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import ImageJob, SerialImageQueue, verify_image
from app.ranking.engine import RankingEngine
from app.ranking.renderer import RankingRenderer
from app.scheduler.period import PeriodResolver
from app.sender.base import WechatSender
from app.sender.wechat_native import create_wechat_sender
from app.services.generation_runtime import generation_mutex
from app.v2.constants import (
    DATA_READY,
    FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    IMAGE_FILE_MISSING,
    MESSAGE_FETCH_FAILED,
    MESSAGE_SNAPSHOT_INVALID,
    PENDING,
    PROMPT_FAILED,
    PROMPT_READY,
    RANKING_FAILED,
    RANKING_READY,
    READY_TO_SEND,
    SENT,
    WECHAT_DATA_UNAVAILABLE,
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
        self.data_source = data_source or WeChatDataAnalysisSource(self.settings)
        self.period_resolver = PeriodResolver()
        self.ranking_engine = ranking_engine or RankingEngine()
        self.renderer = renderer or RankingRenderer()
        self.prompt_builder = prompt_builder or GroupSummaryImagePromptBuilder(self.settings)
        self.image_generator = image_generator or CodexImageGenerator(self.settings)
        self.sender = sender or create_wechat_sender(settings=self.settings, dry_run=dry_run)
        self.store = store or RunStore(self.settings.output_dir)
        self.dry_run = dry_run

    # ================= 生成阶段 =================

    def generate_all(
        self,
        run_date: str | None = None,
        group_ids: list[int] | None = None,
        force: bool = False,
        refresh_messages: bool = False,
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
                    acquire_lock=False,
                )
        requested_date = parse_date(run_date)
        if run_date is not None and requested_date is None:
            return [{
                "status": "failed",
                "error_type": "INVALID_RUN_DATE",
                "detail": "run_date 必须是有效的 YYYY-MM-DD 日期",
            }]
        window = self.period_resolver.resolve(run_date=requested_date, timezone=self.settings.app_timezone)
        run_date_str = window.run_date.isoformat()
        groups = self._load_groups(group_ids)
        if not groups:
            return [{"status": "no_groups", "reason": "无启用群"}]

        group_limit = normalized_limit(self.settings.generation_group_concurrency, 5)
        logger.info(
            "开始并行生成：groups=%d group_limit=%d fetch_limit=%d ai_limit=%d",
            len(groups),
            group_limit,
            normalized_limit(self.settings.wechat_fetch_concurrency, 1),
            normalized_limit(self.settings.ai_request_concurrency, 6),
        )
        if len(groups) == 1:
            group = groups[0]
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
            ) as executor:
                future_indexes = {}
                for index, group in enumerate(groups):
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
                results_by_index: dict[int, dict] = {}
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
                    results_by_index[result_index] = self._run_image_when_ready_safe(
                        group, result, run_date_str, force
                    )

        # 生图按 Prompt 完成顺序启动，API 结果仍按群配置顺序返回。
        return [results_by_index[index] for index in range(len(groups))]

    @staticmethod
    def _group_name(group: Group) -> str:
        return group.display_name or group.wechat_group_name

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
        """单群 Prompt 就绪后立即生图；调用方保证图片任务严格串行。"""
        if not result.get("need_image"):
            return result
        group_name = group.display_name or group.wechat_group_name
        logger.info("群 %s Prompt 已就绪，立即进入串行生图", group_name)
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
    ) -> dict:
        group_name = group.display_name or group.wechat_group_name
        store = self.store
        started_at = perf_counter()
        timings: dict[str, int] = {}

        def finish(result: dict) -> dict:
            timings["total_ms"] = round((perf_counter() - started_at) * 1000)
            current = store.load_run(group_name, run_date)
            meta = current.get("prompt_meta") if isinstance(current.get("prompt_meta"), dict) else {}
            timings["summary_calls"] = int(meta.get("api_call_count") or 0)
            timings["deepseek_calls"] = timings["summary_calls"]  # 旧运行分析字段兼容
            timings["chunk_count"] = int(meta.get("chunk_count") or 0)
            store.update(group_name, run_date, stage_timings=timings)
            logger.info(
                "群生成耗时 group=%s fetch_ms=%d ranking_ms=%d summary_ms=%d "
                "summary_calls=%d chunks=%d total_ms=%d status=%s",
                group_name,
                timings.get("fetch_ms", 0),
                timings.get("ranking_ms", 0),
                timings.get("summary_ms", timings.get("deepseek_ms", 0)),
                timings["summary_calls"],
                timings["chunk_count"],
                timings["total_ms"],
                result.get("status", ""),
            )
            return result

        # 防重复：同一群同一周期已到终态
        run = store.load_run(group_name, run_date)
        if not force and not refresh_messages and run.get("status") in (IMAGE_READY, READY_TO_SEND, SENT):
            logger.info("群 %s %s 已到 %s，跳过生成", group_name, run_date, run.get("status"))
            return finish({"group_name": group_name, "status": "skipped", "detail": f"已{run.get('status')}"})

        period_start = window.period_start_str()
        period_end = window.period_end_str()
        base = {
            "group_id": str(group.id),
            "wechat_group_id": group.wechat_group_id,
            "period_start": period_start,
            "period_end": period_end,
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
        store.update(group_name, run_date, status=PENDING, **base)

        # ---- 1) 数据快照（同一日报日期默认只读取一次）----
        if not group.wechat_group_id:
            store.update(group_name, run_date, status=FAILED, failed_stage="data", error="群未绑定微信群 ID")
            return finish({"group_name": group_name, "status": "failed", "error_type": WECHAT_DATA_UNAVAILABLE})
        fetch_started = perf_counter()
        snapshot_path = store.messages_path(group_name, run_date)
        messages: list[V2Message]
        if snapshot_path.is_file() and not refresh_messages:
            try:
                messages = self._load_message_snapshot(snapshot_path)
            except (OSError, UnicodeError, ValueError) as exc:
                timings["fetch_ms"] = round((perf_counter() - fetch_started) * 1000)
                detail = f"当天消息快照无法读取，已停止且不会隐式重抓：{str(exc)[:220]}"
                store.update(
                    group_name,
                    run_date,
                    status=FAILED,
                    failed_stage="data",
                    error=detail,
                    error_type=MESSAGE_SNAPSHOT_INVALID,
                    message_snapshot_reused=False,
                )
                return finish({
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": MESSAGE_SNAPSHOT_INVALID,
                    "detail": detail,
                })
            timings["fetch_ms"] = round((perf_counter() - fetch_started) * 1000)
            store.update(
                group_name,
                run_date,
                status=DATA_READY,
                message_count=len(messages),
                message_snapshot_reused=True,
                message_snapshot_refreshed=False,
                message_snapshot_path=snapshot_path.name,
            )
            logger.info("群 %s 复用当天消息快照：%s（%d 条）", group_name, snapshot_path, len(messages))
        else:
            try:
                with bounded_slot("wechat_fetch", self.settings.wechat_fetch_concurrency):
                    fetch = self.data_source.fetch_messages(
                        group.wechat_group_id, window.period_start, window.period_end
                    )
            except Exception as exc:
                timings["fetch_ms"] = round((perf_counter() - fetch_started) * 1000)
                store.update(
                    group_name,
                    run_date,
                    status=FAILED,
                    failed_stage="data",
                    error=str(exc)[:300],
                    error_type=MESSAGE_FETCH_FAILED,
                )
                return finish({
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": MESSAGE_FETCH_FAILED,
                    "detail": str(exc)[:300],
                })
            timings["fetch_ms"] = round((perf_counter() - fetch_started) * 1000)
            fetch_metrics = fetch.meta if isinstance(getattr(fetch, "meta", None), dict) else {}
            if fetch_metrics:
                store.update(group_name, run_date, fetch_metrics=fetch_metrics)
            if fetch.status.value != "OK" or not fetch.messages:
                error_type = fetch.error_type or MESSAGE_FETCH_FAILED
                store.update(group_name, run_date, status=FAILED, failed_stage="data",
                             error=fetch.detail or fetch.status.value, error_type=error_type)
                return finish({"group_name": group_name, "status": "failed", "error_type": error_type,
                        "detail": fetch.detail})
            messages = list(fetch.messages)
            if not refresh_messages:
                self._save_json(snapshot_path, [m.to_dict() for m in messages])
                store.update(
                    group_name,
                    run_date,
                    status=DATA_READY,
                    message_count=len(messages),
                    message_snapshot_reused=False,
                    message_snapshot_refreshed=False,
                    message_snapshot_saved_at=datetime.now().astimezone().isoformat(),
                    message_snapshot_path=snapshot_path.name,
                    message_snapshot_period_start=period_start,
                    message_snapshot_period_end=period_end,
                )

        if refresh_messages:
            # 手动重取消息只更新消息与确定性排行榜。先在内存中完成计算和渲染，
            # 成功后再落盘，避免留下“新消息 + 旧排行榜”的明显不一致状态。
            ranking_started = perf_counter()
            try:
                ranking = self.ranking_engine.compute(
                    messages,
                    group_name,
                    period_start,
                    period_end,
                    top_limit=10,
                )
                ranking_txt = self.renderer.render(
                    ranking, template_name=group.ranking_template
                )
            except Exception as exc:
                timings["ranking_ms"] = round((perf_counter() - ranking_started) * 1000)
                store.update(
                    group_name,
                    run_date,
                    status=run.get("status") or PENDING,
                    failed_stage=run.get("failed_stage"),
                    error=run.get("error"),
                    message_refresh_status="failed",
                    message_refresh_error=str(exc)[:300],
                )
                return finish({
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": RANKING_FAILED,
                    "detail": "新消息已读取，但排行榜计算失败，旧快照未被替换",
                })
            timings["ranking_ms"] = round((perf_counter() - ranking_started) * 1000)

            self._save_json(snapshot_path, [m.to_dict() for m in messages])
            self._save_json(store.ranking_json_path(group_name, run_date), ranking.to_dict())
            store.ranking_txt_path(group_name, run_date).write_text(ranking_txt, encoding="utf-8")
            next_status = SENT if run.get("status") == SENT else RANKING_READY
            store.update(
                group_name,
                run_date,
                status=next_status,
                failed_stage=None,
                error=None,
                speaker_count=ranking.speaker_count,
                message_count=ranking.message_count,
                message_snapshot_reused=False,
                message_snapshot_refreshed=True,
                message_snapshot_saved_at=datetime.now().astimezone().isoformat(),
                message_snapshot_path=snapshot_path.name,
                message_snapshot_period_start=period_start,
                message_snapshot_period_end=period_end,
                message_refresh_status="completed",
                message_refresh_error="",
                prompt_rebuild_status="required",
                prompt_rebuild_error="",
                send_hold=True,
                send_hold_reason="MESSAGE_SNAPSHOT_REFRESHED",
                needs_manual_send=True,
            )
            return finish({
                "group_name": group_name,
                "status": "data_ready",
                "detail": "当天消息快照和排行榜已更新；未重建 Prompt，未生图",
            })

        # ---- 2) 排行榜 ----
        ranking_started = perf_counter()
        try:
            ranking = self.ranking_engine.compute(
                messages,
                group_name,
                period_start,
                period_end,
                top_limit=10,
            )
        except Exception as e:
            store.update(group_name, run_date, status=FAILED, failed_stage="ranking", error=str(e)[:300])
            timings["ranking_ms"] = round((perf_counter() - ranking_started) * 1000)
            return finish({"group_name": group_name, "status": "failed", "error_type": RANKING_FAILED})
        timings["ranking_ms"] = round((perf_counter() - ranking_started) * 1000)
        self._save_json(store.ranking_json_path(group_name, run_date), ranking.to_dict())
        ranking_txt = self.renderer.render(ranking, template_name=group.ranking_template)
        store.ranking_txt_path(group_name, run_date).write_text(ranking_txt, encoding="utf-8")
        store.update(group_name, run_date, status=RANKING_READY,
                     speaker_count=ranking.speaker_count, message_count=ranking.message_count)

        # ---- 3) 生图 Prompt（Codex GPT 主用，DeepSeek 备用）----
        prompt_msgs = [m for m in messages if RankingEngine._countable(m)]
        prompt_input = PromptInput(
            group_name=group_name,
            group_id=str(group.id or group.wechat_group_id or group_name),
            run_date=run_date,
            period_start=period_start,
            period_end=period_end,
            report_date=window.period_end.date().isoformat(),
            message_count=ranking.message_count,
            speaker_count=ranking.speaker_count,
            messages=prompt_msgs,
            template=group.image_prompt_template,
            image_theme=group.image_theme,
            image_theme_custom=group.image_theme_custom,
            template_override=getattr(group, "image_prompt_override", "") or "",
            previous_theme_signature=store.previous_theme_signature(group_name, run_date),
            persisted_theme_meta=run.get("prompt_meta") if isinstance(run.get("prompt_meta"), dict) else None,
            recent_layout_history=store.recent_layout_history(group_name, run_date, limit=3),
        )
        prompt_started = perf_counter()
        prompt_out = self.prompt_builder.build(prompt_input)
        timings["summary_ms"] = round((perf_counter() - prompt_started) * 1000)
        timings["deepseek_ms"] = timings["summary_ms"]  # 旧运行分析字段兼容
        if not prompt_out.success:
            store.update(group_name, run_date, status=FAILED, failed_stage="prompt",
                         error=prompt_out.error, error_type=PROMPT_FAILED)
            return finish({"group_name": group_name, "status": "failed", "error_type": PROMPT_FAILED,
                    "detail": prompt_out.error})
        store.prompt_path(group_name, run_date).write_text(prompt_out.prompt, encoding="utf-8")
        store.update(group_name, run_date, status=PROMPT_READY, prompt_meta=prompt_out.meta)

        # Prompt 落盘后重新读取群配置，避免流水线开始后用户打开/关闭生图
        # 时仍使用旧的 group 对象和 image_enabled 快照。
        current_group = group
        if group.id is not None:
            try:
                refreshed_group = self._get_group(group.id)
            except Exception as exc:
                logger.warning("群 %s 生图开关刷新失败，沿用本次读取的配置：%s", group_name, exc)
            else:
                if refreshed_group is not None:
                    current_group = refreshed_group

        current_image_enabled = bool(current_group.image_enabled)
        group.image_enabled = current_image_enabled
        store.update(group_name, run_date, image_enabled=current_image_enabled)

        # ---- 4) 生图判断 ----
        if not current_image_enabled:
            store.update(group_name, run_date, status=READY_TO_SEND)
            return finish({"group_name": group_name, "status": "ready_to_send", "detail": "未启用生图"})
        return finish({"group_name": group_name, "status": "prompt_ready", "need_image": True})

    def _make_image_job(self, group: Group, run_date: str, force: bool) -> ImageJob:
        group_name = group.display_name or group.wechat_group_name
        return ImageJob(
            group_name=group_name,
            prompt_file=self.store.prompt_path(group_name, run_date),
            output_path=self.store.image_path(group_name, run_date),
            generator=self.image_generator,
            force=force,
        )

    def _image_hook(self, job: ImageJob, result: dict) -> None:
        # 每群生图完成后更新 run.json（不在此处判断 need_image）
        status = IMAGE_READY if result["success"] else FAILED
        error_type = result.get("error_type") or IMAGE_GENERATION_FAILED
        current = self.store.load_run(job.group_name, job.output_path.parent.name)
        stage_timings = dict(current.get("stage_timings") or {})
        imagegen_ms = int(result.get("imagegen_ms") or 0)
        stage_timings["imagegen_ms"] = imagegen_ms
        image_size_bytes = job.output_path.stat().st_size if result["success"] and job.output_path.is_file() else 0
        generator_detail = result.get("generator_detail")
        if not isinstance(generator_detail, dict):
            generator_detail = {}
        self.store.update(
            job.group_name, job.output_path.parent.name,
            status=status,
            image_error=result.get("detail") if not result["success"] else None,
            image_status=result["status"],
            error_type=error_type if not result["success"] else None,
            stage_timings=stage_timings,
            imagegen_ms=imagegen_ms,
            image_generated_at=(
                datetime.now().astimezone().isoformat()
                if result["success"]
                else current.get("image_generated_at")
            ),
            image_size_bytes=image_size_bytes,
            image_attempt_count=int(generator_detail.get("attempt_count") or 0),
            image_recovery_status=str(generator_detail.get("recovery_status") or ""),
            image_candidate_diagnostics=generator_detail.get("candidate_diagnostics") or [],
            image_attempts=generator_detail.get("attempts") or [],
        )

    def _after_image(self, job: ImageJob, run_date: str) -> None:
        run = self.store.load_run(job.group_name, run_date)
        if run.get("status") == IMAGE_READY:
            self.store.update(job.group_name, run_date, status=READY_TO_SEND)

    def _run_image_jobs(self, image_jobs: list[ImageJob], run_date: str) -> list[dict]:
        """串行执行图片任务，并以每个 run.json 的最终状态返回结果。"""
        queue = SerialImageQueue(run_hook=self._image_hook)
        queue_results = queue.run_all(image_jobs)
        final_results: list[dict] = []
        for job, queue_result in zip(image_jobs, queue_results):
            self._after_image(job, run_date)
            run = self.store.load_run(job.group_name, run_date)
            final_status = run.get("status")
            if final_status == READY_TO_SEND:
                final_results.append(
                    {
                        "group_name": job.group_name,
                        "status": "ready_to_send",
                        "detail": "图片已准备，可以发送",
                    }
                )
                continue
            if final_status == FAILED:
                final_results.append(
                    {
                        "group_name": job.group_name,
                        "status": "failed",
                        "error_type": run.get("error_type") or queue_result.get("error_type") or IMAGE_GENERATION_FAILED,
                        "detail": run.get("image_error") or run.get("error") or queue_result.get("detail") or "生图失败",
                    }
                )
                continue
            final_results.append(
                {
                    "group_name": job.group_name,
                    "status": str(final_status or queue_result.get("status") or "failed").lower(),
                    "detail": queue_result.get("detail") or "图片任务未进入终态",
                }
            )
        return final_results

    # ================= 发送阶段 =================

    def send_due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(ZoneInfo(self.settings.app_timezone))
        run_date = now.date().isoformat()
        results: list[dict] = []
        for group in self._load_groups():
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
                continue  # 手工 Prompt/重生图必须先审核
            send_time = parse_send_time(group.send_time or run.get("send_time", "08:30"))
            if now.time() < send_time:
                continue  # 未到发送时间
            due_at = datetime.combine(now.date(), send_time, tzinfo=now.tzinfo)
            late_window = timedelta(minutes=max(int(self.settings.wechat_late_send_window_minutes), 0))
            if now > due_at + late_window:
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
            result = self._send_one(group, group_name, run, run_date, now)
            results.append(result)
        return results

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
        claim_id, run, claim_reason = self.store.claim_send(
            group_name,
            run_date,
            now=now,
            lease_seconds=self.settings.wechat_send_claim_seconds,
            allow_hold=allow_hold,
            allow_sent=allow_sent,
        )
        if not claim_id:
            if claim_reason == "result_unknown":
                return {
                    "group_name": group_name,
                    "status": "held",
                    "error_type": "SEND_RESULT_UNKNOWN",
                    "detail": "上次发送结果未知，已暂停自动重试",
                }
            return {
                "group_name": group_name,
                "status": "skipped",
                "detail": f"发送任务未领取：{claim_reason}",
            }

        target = group.send_target or group.wechat_group_name or group_name
        ranking_txt = self.store.ranking_txt_path(group_name, run_date)
        image = self.store.image_path(group_name, run_date)

        try:
            ranking_text = ranking_txt.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.store.finish_send_claim(
                group_name, run_date, claim_id, send_state="failed",
                status=FAILED, failed_stage="send", error="ranking.txt 缺失或无法读取",
                error_type="SEND_TEXT_FAILED",
            )
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "SEND_TEXT_FAILED",
                "detail": "ranking.txt 缺失或无法读取",
            }
        if not ranking_text.strip():
            self.store.finish_send_claim(
                group_name, run_date, claim_id, send_state="failed",
                status=FAILED, failed_stage="send", error="ranking.txt 为空",
                error_type="SEND_TEXT_FAILED",
            )
            return {
                "group_name": group_name,
                "status": "failed",
                "error_type": "SEND_TEXT_FAILED",
                "detail": "ranking.txt 为空",
            }

        image_enabled = bool(group.image_enabled)
        if image_enabled:
            image_ok, image_detail = verify_image(image)
            if not image_ok:
                self.store.finish_send_claim(
                    group_name, run_date, claim_id, send_state="failed",
                    status=FAILED, failed_stage="send", error=image_detail,
                    error_type=IMAGE_FILE_MISSING,
                )
                return {
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": IMAGE_FILE_MISSING,
                    "detail": image_detail,
                }

        # 图片曾失败而文字已经确认时，只补发图片，避免分钟级重试重复发文字。
        text_already_sent = bool(run.get("text_sent_at"))
        image_path = str(image.resolve()) if image_enabled else None
        text_sent_at = str(run.get("text_sent_at") or "")
        verification_levels: list[str] = []

        if not text_already_sent:
            started_at = datetime.now(now.tzinfo).isoformat()
            updated, _ = self.store.update_send_claim(
                group_name,
                run_date,
                claim_id,
                send_state="sending_text",
                text_attempt_started_at=started_at,
                text_attempt_finished_at="",
                text_submitted_at="",
                text_verified_at="",
            )
            if not updated:
                return {"group_name": group_name, "status": "skipped", "detail": "发送 claim 已失效"}
            try:
                text_result = self.sender.send_text(target, ranking_text)
            except Exception as exc:
                return self._finish_unknown_send(
                    group_name, run_date, claim_id, "text", f"文字发送异常：{exc}"
                )
            finished_at = datetime.now(now.tzinfo).isoformat()
            if text_result.outcome_unknown:
                return self._finish_unknown_send(
                    group_name, run_date, claim_id, "text", text_result.detail,
                    submitted_at=finished_at if text_result.submitted else "",
                )
            if not text_result.success:
                self.store.finish_send_claim(
                    group_name,
                    run_date,
                    claim_id,
                    send_state="ready",
                    status=run.get("status", READY_TO_SEND),
                    text_attempt_finished_at=finished_at,
                    text_submitted_at=finished_at if text_result.submitted else "",
                    send_error=text_result.detail,
                    send_error_type="SEND_TEXT_FAILED",
                )
                return {
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": "SEND_TEXT_FAILED",
                    "detail": text_result.detail,
                }
            text_sent_at = text_result.sent_at or finished_at
            text_level = text_result.verification_level or "provider_reported"
            verification_levels.append(text_level)
            self.store.update_send_claim(
                group_name,
                run_date,
                claim_id,
                send_state="text_verified",
                text_attempt_finished_at=finished_at,
                text_submitted_at=finished_at if text_result.submitted or text_result.success else "",
                text_verified_at=finished_at,
                text_sent_at=text_sent_at,
                text_verification_level=text_level,
                send_error="",
                send_error_type="",
            )
        elif run.get("text_verification_level"):
            verification_levels.append(str(run["text_verification_level"]))

        image_sent_at = str(run.get("image_sent_at") or "")
        if image_enabled:
            started_at = datetime.now(now.tzinfo).isoformat()
            self.store.update_send_claim(
                group_name,
                run_date,
                claim_id,
                send_state="sending_image",
                image_attempt_started_at=started_at,
                image_attempt_finished_at="",
                image_submitted_at="",
                image_verified_at="",
            )
            try:
                image_result = self.sender.send_image(target, image_path)
            except Exception as exc:
                return self._finish_unknown_send(
                    group_name, run_date, claim_id, "image", f"图片发送异常：{exc}"
                )
            finished_at = datetime.now(now.tzinfo).isoformat()
            if image_result.outcome_unknown:
                return self._finish_unknown_send(
                    group_name, run_date, claim_id, "image", image_result.detail,
                    submitted_at=finished_at if image_result.submitted else "",
                )
            if not image_result.success:
                self.store.finish_send_claim(
                    group_name,
                    run_date,
                    claim_id,
                    send_state="ready",
                    status=run.get("status", READY_TO_SEND),
                    text_sent_at=text_sent_at,
                    image_attempt_finished_at=finished_at,
                    image_submitted_at=finished_at if image_result.submitted else "",
                    send_error=image_result.detail,
                    send_error_type="SEND_IMAGE_FAILED",
                )
                return {
                    "group_name": group_name,
                    "status": "failed",
                    "error_type": "SEND_IMAGE_FAILED",
                    "detail": image_result.detail,
                }
            image_sent_at = image_result.sent_at or finished_at
            image_level = image_result.verification_level or "provider_reported"
            verification_levels.append(image_level)
            self.store.update_send_claim(
                group_name,
                run_date,
                claim_id,
                send_state="image_verified",
                image_attempt_finished_at=finished_at,
                image_submitted_at=finished_at if image_result.submitted or image_result.success else "",
                image_verified_at=finished_at,
                image_sent_at=image_sent_at,
                image_verification_level=image_level,
                send_error="",
                send_error_type="",
            )

        if verification_levels and all(level == "ui_observed" for level in verification_levels):
            verification_level = "ui_observed"
        elif verification_levels and all(level == "dry_run" for level in verification_levels):
            verification_level = "dry_run"
        else:
            verification_level = "provider_reported"
        self.store.finish_send_claim(
            group_name,
            run_date,
            claim_id,
            send_state="sent",
            status=SENT,
            sent_at=now.isoformat(),
            sent_target=target,
            text_sent_at=text_sent_at,
            image_sent_at=image_sent_at,
            send_error="",
            send_error_type="",
            verification_level=verification_level,
            send_hold=False,
            send_hold_reason="",
            needs_manual_send=False,
            image_regen_status="sent" if run.get("image_regen_status") == "ready_for_review" else run.get("image_regen_status"),
        )
        if image_enabled:
            logger.info("群 %s 已发送（文字+图片）→ SENT", group_name)
            detail = "文字和图片已发送"
        else:
            logger.info("群 %s 已发送（仅文字，未启用图片）→ SENT", group_name)
            detail = "文字已发送（未启用图片）"
        return {"group_name": group_name, "status": "sent", "detail": detail, "sent_at": now.isoformat()}

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
        finished_at = datetime.now(ZoneInfo(self.settings.app_timezone)).isoformat()
        fields = {
            f"{stage}_attempt_finished_at": "",
            f"{stage}_submitted_at": submitted_at,
        }
        self.store.finish_send_claim(
            group_name,
            run_date,
            claim_id,
            send_state="unknown",
            send_hold=True,
            send_hold_reason="SEND_RESULT_UNKNOWN",
            needs_manual_send=True,
            send_error=detail,
            send_error_type="SEND_RESULT_UNKNOWN",
            verification_level="unknown",
            send_unknown_at=finished_at,
            **fields,
        )
        return {
            "group_name": group_name,
            "status": "held",
            "error_type": "SEND_RESULT_UNKNOWN",
            "detail": detail,
        }

    # ================= 手动操作 =================

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
        acquire_lock: bool = True,
    ) -> dict:
        """只从当天 messages.json 重建排行榜和 Prompt，不取数、不生图。"""
        if acquire_lock:
            with generation_mutex():
                return self.rebuild_prompt_from_snapshot(
                    group_id,
                    run_date,
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

        keep_sent = current.get("status") == SENT
        self.store.update(
            group_name,
            run_date,
            prompt_rebuild_status="running",
            prompt_rebuild_error="",
            send_hold=True,
            send_hold_reason="PROMPT_REBUILDING",
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
        )
        if result.get("status") == "failed":
            self.store.update(
                group_name,
                run_date,
                prompt_rebuild_status="failed",
                prompt_rebuild_error=str(result.get("detail") or result.get("error") or "重建失败")[:500],
                send_hold=True,
                needs_manual_send=True,
            )
            return result

        self.store.update(
            group_name,
            run_date,
            status=SENT if keep_sent else PROMPT_READY,
            prompt_rebuild_status="ready_for_review",
            prompt_rebuild_error="",
            image_regen_status="prompt_rebuilt",
            image_regen_error="",
            send_hold=True,
            send_hold_reason="PROMPT_REBUILT_REVIEW_REQUIRED",
            needs_manual_send=True,
        )
        return {
            "group_name": group_name,
            "status": "prompt_ready",
            "detail": "已从当天 messages.json 重建排行榜和 Prompt；未取数，未生图",
        }

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

    def _load_groups(self, group_ids: list[int] | None = None) -> list[Group]:
        from sqlmodel import Session

        with Session(repo.engine) as session:
            groups = repo.list_groups(session, only_enabled=True)
        if group_ids:
            groups = [g for g in groups if g.id in group_ids]
        return groups

    def _get_group(self, group_id: int) -> Group | None:
        from sqlmodel import Session

        with Session(repo.engine) as session:
            return repo.get_active_group(session, group_id)

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
