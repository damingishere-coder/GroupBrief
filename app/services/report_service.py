"""群报生成服务：一次任务（Run）→ 每个群（GroupRun）→ 排行 + Prompt（可选）。

防重复：同一 report_date + group + range 已成功时不重复生成；
force=True 时允许重新生成。
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

from app.core.logging import get_logger
from app.db import repository as repo
from app.db.models import Group, GroupRun, Report, Run
from app.scheduler.calendar_rules import ReportWindow, get_report_window
from app.services.history_service import HistoryService
from app.services.message_normalizer import normalize_messages
from app.services.ranking_service import RankingEngine
from app.services.prompt_service import PromptService

logger = get_logger("app")


class ReportService:
    def __init__(self, history: HistoryService | None = None, prompt: PromptService | None = None):
        self.history = history or HistoryService()
        self.prompt = prompt or PromptService()
        self.ranking = RankingEngine()

    def generate(
        self,
        session: Session,
        group: Group | None = None,
        report_date: str | None = None,
        trigger_type: str = "manual",
        force: bool = False,
    ) -> Run:
        """生成群报。group=None 表示全部启用群。"""
        window: ReportWindow = get_report_window(
            datetime.strptime(report_date, "%Y-%m-%d").date() if report_date else None
        )
        if not window.should_run:
            run = repo.create_run(
                session,
                Run(
                    report_date=window.report_date.isoformat(),
                    range_start=window.range_start.isoformat(),
                    range_end=window.range_end.isoformat(),
                    trigger_type=trigger_type,
                    status="skipped",
                    error_message="周日不执行",
                ),
            )
            return run

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

        success_count = 0
        fail_count = 0
        errors: list[str] = []
        for g in groups:
            try:
                group_run = self._generate_one(
                    session, run, g, window, force=force
                )
                if group_run.ranking_status == "success":
                    success_count += 1
                else:
                    fail_count += 1
                    errors.append(f"{g.display_name}: {group_run.error_message}")
            except Exception as e:
                fail_count += 1
                errors.append(f"{g.display_name}: {str(e)[:200]}")
                logger.exception("生成失败 group=%s", g.display_name)
                session.add(
                    GroupRun(
                        run_id=run.id,
                        group_id=g.id,
                        ranking_status="failed",
                        prompt_status="failed",
                        error_message=str(e)[:500],
                    )
                )
                session.commit()

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

    def _generate_one(
        self,
        session: Session,
        run: Run,
        group: Group,
        window: ReportWindow,
        force: bool,
    ) -> GroupRun:
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
        outcome = self.history.fetch(
            group.wechat_group_id or wechat_id,
            group.wechat_group_name or group.display_name,
            window.range_start,
            window.range_end,
        )
        group_run.provider_used = outcome.provider
        if not outcome.messages:
            group_run.ranking_status = "failed"
            group_run.prompt_status = "skipped"
            group_run.error_message = f"读取失败：{outcome.status.value} {outcome.detail[:300]}"
            session.add(group_run)
            session.commit()
            return group_run

        # 2. 标准化
        normalized = normalize_messages(outcome.messages)

        # 3. 排行榜
        ranking = self.ranking.compute(
            normalized, group.display_name or group.wechat_group_name,
            range_start, range_end,
        )
        group_run.message_count = ranking.total_messages
        group_run.speaker_count = ranking.speaker_count
        group_run.ranking_status = "success"
        session.add(group_run)
        session.commit()

        # 4. Prompt（P4 实现；无 API Key 时标记 skipped）
        prompt_result = self.prompt.generate(
            group=group,
            window=window,
            ranking=ranking,
            normalized=normalized,
        )
        if prompt_result.success:
            group_run.prompt_status = "success"
        else:
            group_run.prompt_status = "skipped"
            if prompt_result.error:
                group_run.error_message = prompt_result.error[:300]

        # 5. 保存 Report
        report = repo.get_report_by_group_run(session, group_run.id)
        if report is None:
            report = Report(group_run_id=group_run.id)
        report.ranking_text = ranking.render()
        report.prompt_text = prompt_result.prompt
        if prompt_result.success:
            report.prompt_status = "ready"
        repo.save_report(session, report)

        session.add(group_run)
        session.commit()
        return group_run

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
