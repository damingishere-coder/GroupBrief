"""群报生成服务：一次任务（Run）→ 每个群（GroupRun）→ 排行 + Prompt（可选）。

防重复：同一 report_date + group + range 已成功时不重复生成；
force=True 时允许重新生成。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter

from sqlmodel import Session

from app.ai.concurrency import bounded_slot, normalized_limit
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db import repository as repo
from app.db.models import Group, GroupRun, Report, Run
from app.scheduler.calendar_rules import ReportWindow, get_report_window
from app.services.handoff_service import HandoffService
from app.services.history_service import HistoryService
from app.services.message_normalizer import normalize_messages
from app.services.ranking_service import RankingEngine
from app.services.prompt_service import PromptService
from app.services.generation_runtime import generation_mutex
from app.services.legacy_v1_policy import require_legacy_v1_write

logger = get_logger("app")


class ReportService:
    def __init__(
        self,
        history: HistoryService | None = None,
        prompt: PromptService | None = None,
        *,
        settings: Settings | None = None,
    ):
        self.history = history or HistoryService()
        self.prompt = prompt or PromptService()
        self.settings = settings or get_settings()
        self.ranking = RankingEngine()
        self.handoff = HandoffService()

    def generate(
        self,
        session: Session,
        group: Group | None = None,
        report_date: str | None = None,
        trigger_type: str = "manual",
        force: bool = False,
        *,
        acquire_lock: bool = True,
    ) -> Run:
        """生成群报。group=None 表示全部启用群。"""
        require_legacy_v1_write(
            self.settings,
            operation="report.generate",
            replacement="POST /api/v2/pipeline/generate",
        )
        if acquire_lock:
            with generation_mutex():
                return self.generate(
                    session,
                    group=group,
                    report_date=report_date,
                    trigger_type=trigger_type,
                    force=force,
                    acquire_lock=False,
                )
        window: ReportWindow = get_report_window(
            datetime.strptime(report_date, "%Y-%m-%d").date() if report_date else None
        )
        groups = [group] if group else repo.list_groups(session, only_enabled=True)
        if not groups:
            run = repo.create_run(
                session,
                Run(
                    report_date=window.report_date.isoformat(),
                    range_start=window.range_start.isoformat(),
                    range_end=window.range_end.isoformat(),
                    trigger_type=trigger_type,
                    status="failed",
                    error_message="没有可用的启用群",
                ),
            )
            return run

        run = repo.create_run(
            session,
            Run(
                report_date=window.report_date.isoformat(),
                range_start=window.range_start.isoformat(),
                range_end=window.range_end.isoformat(),
                trigger_type=trigger_type,
            ),
        )

        group_specs = [(int(g.id), g.display_name or g.wechat_group_name) for g in groups if g.id is not None]
        settings = self.settings
        group_limit = normalized_limit(settings.generation_group_concurrency, 5)
        logger.info(
            "V1 开始并行生成：groups=%d group_limit=%d fetch_limit=%d ai_limit=%d",
            len(group_specs),
            group_limit,
            normalized_limit(settings.wechat_fetch_concurrency, 1),
            normalized_limit(settings.ai_request_concurrency, 6),
        )
        if len(group_specs) == 1:
            outcomes = [self._generate_group_worker(run.id, group_specs[0][0], window, force)]
        else:
            with ThreadPoolExecutor(
                max_workers=min(group_limit, len(group_specs)),
                thread_name_prefix="groupbrief-v1-group",
            ) as executor:
                futures = [
                    executor.submit(self._generate_group_worker, run.id, group_id, window, force)
                    for group_id, _ in group_specs
                ]
                outcomes = [future.result() for future in futures]

        success_count = sum(1 for outcome in outcomes if outcome[0])
        fail_count = len(outcomes) - success_count
        errors = [
            f"{group_name}: {detail}"
            for (success, detail), (_, group_name) in zip(outcomes, group_specs)
            if not success and detail
        ]

        if fail_count == 0:
            run.status = "success"
        elif success_count == 0:
            run.status = "failed"
        else:
            run.status = "partial"
        run.finished_at = datetime.now()
        run.error_message = "；".join(errors)[:500]
        repo.update_run(session, run)
        logger.info("run %s 完成：status=%s success=%d fail=%d", run.id, run.status, success_count, fail_count)
        return run

    def _generate_group_worker(
        self,
        run_id: int,
        group_id: int,
        window: ReportWindow,
        force: bool,
    ) -> tuple[bool, str]:
        """一个 worker 独占一个数据库 Session，异常不外溢到其他群。"""
        with Session(repo.engine) as worker_session:
            run = repo.get_run(worker_session, run_id)
            group = repo.get_active_group(worker_session, group_id)
            if run is None or group is None:
                return False, "运行或群配置不存在"
            try:
                group_run = self._generate_one(worker_session, run, group, window, force=force)
                if group_run.ranking_status == "success" and group_run.prompt_status == "success":
                    return True, group_run.error_message
                return False, group_run.error_message or "生成失败"
            except Exception as exc:
                logger.exception("生成失败 group=%s", group.display_name)
                worker_session.add(
                    GroupRun(
                        run_id=run.id,
                        group_id=group.id,
                        ranking_status="failed",
                        prompt_status="failed",
                        error_message=str(exc)[:500],
                    )
                )
                worker_session.commit()
                return False, str(exc)[:200]

    def _generate_one(
        self,
        session: Session,
        run: Run,
        group: Group,
        window: ReportWindow,
        force: bool,
    ) -> GroupRun:
        started_at = perf_counter()
        fetch_ms = 0
        ranking_ms = 0
        summary_ms = 0
        # 防重复：同 group + report_date + range 已成功则跳过
        existing = self._find_success_group_run(session, group.id, run)
        if existing and not force:
            return existing

        group_run = GroupRun(
            run_id=run.id,
            group_id=group.id,
            ranking_status="running",
            prompt_status="pending",
        )
        session.add(group_run)
        session.commit()
        session.refresh(group_run)

        range_start = window.range_start.strftime("%Y-%m-%d %H:%M:%S")
        range_end = window.range_end.strftime("%Y-%m-%d %H:%M:%S")

        # 1. 读取聊天
        wechat_id = group.wechat_group_id or group.wechat_group_name or group.display_name
        fetch_started = perf_counter()
        with bounded_slot("wechat_fetch", self.settings.wechat_fetch_concurrency):
            outcome = self.history.fetch(
                group.wechat_group_id or wechat_id,
                group.wechat_group_name or group.display_name,
                window.range_start,
                window.range_end,
            )
        fetch_ms = round((perf_counter() - fetch_started) * 1000)
        group_run.provider_used = outcome.provider
        if not outcome.messages:
            group_run.ranking_status = "failed"
            group_run.prompt_status = "skipped"
            group_run.error_message = f"读取失败：{outcome.status.value} {outcome.detail[:300]}"
            session.add(group_run)
            session.commit()
            self._log_timings(group, fetch_ms, ranking_ms, summary_ms, {}, started_at, "failed")
            return group_run

        # 2. 标准化
        normalized = normalize_messages(outcome.messages)

        # 3. 排行榜
        ranking_started = perf_counter()
        ranking = self.ranking.compute(
            normalized, group.display_name or group.wechat_group_name,
            range_start, range_end,
        )
        ranking_ms = round((perf_counter() - ranking_started) * 1000)
        group_run.message_count = ranking.total_messages
        group_run.speaker_count = ranking.speaker_count
        group_run.ranking_status = "success"
        session.add(group_run)
        session.commit()

        # 4. Prompt（P4 实现；无 API Key 时标记 skipped）
        prompt_started = perf_counter()
        prompt_result = self.prompt.generate(
            group=group,
            window=window,
            ranking=ranking,
            normalized=normalized,
        )
        summary_ms = round((perf_counter() - prompt_started) * 1000)
        if prompt_result.success:
            group_run.prompt_status = "success"
        else:
            # 模板降级在 PromptService 内部已完成；到这里仍失败就必须让本群失败，
            # 禁止用残缺摘要伪装为成功日报。
            group_run.prompt_status = "failed"
            if prompt_result.error:
                group_run.error_message = prompt_result.error[:300]

        # 5. 保存 Report
        report = repo.get_report_by_group_run(session, group_run.id)
        if report is None:
            report = Report(group_run_id=group_run.id)
        report.ranking_text = ranking.render()
        report.prompt_text = prompt_result.prompt
        repo.save_report(session, report)

        # 6. 本地文件输出 + V2 Handoff
        try:
            output_dir = self.handoff.save_outputs(
                group=group,
                window=window,
                ranking=ranking,
                prompt_text=prompt_result.prompt,
                normalized=normalized,
                provider=outcome.provider,
            )
            report.ranking_file = str(output_dir / "ranking.txt")
            report.prompt_file = str(output_dir / "image_prompt.txt")
            repo.save_report(session, report)
        except Exception as e:
            logger.exception("文件输出失败 group=%s", group.display_name)
            group_run.error_message = f"文件输出失败：{str(e)[:200]}"

        session.add(group_run)
        session.commit()
        self._log_timings(
            group,
            fetch_ms,
            ranking_ms,
            summary_ms,
            prompt_result.meta or {},
            started_at,
            group_run.ranking_status,
        )
        return group_run

    @staticmethod
    def _log_timings(
        group: Group,
        fetch_ms: int,
        ranking_ms: int,
        summary_ms: int,
        meta: dict,
        started_at: float,
        status: str,
    ) -> None:
        logger.info(
            "V1 群生成耗时 group=%s fetch_ms=%d ranking_ms=%d summary_ms=%d "
            "summary_calls=%d chunks=%d total_ms=%d status=%s",
            group.display_name or group.wechat_group_name,
            fetch_ms,
            ranking_ms,
            summary_ms,
            int(meta.get("api_call_count") or 0),
            int(meta.get("chunk_count") or 0),
            round((perf_counter() - started_at) * 1000),
            status,
        )

    def _find_success_group_run(
        self, session: Session, group_id: int, run: Run
    ) -> GroupRun | None:
        from sqlmodel import select

        stmt = (
            select(GroupRun)
            .join(Run, GroupRun.run_id == Run.id)
            .where(
                GroupRun.group_id == group_id,
                GroupRun.ranking_status == "success",
                Run.report_date == run.report_date,
                Run.range_start == run.range_start,
                Run.range_end == run.range_end,
            )
            .order_by(GroupRun.id.desc())
        )
        return session.exec(stmt).first()
