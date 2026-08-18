"""V2 每日全流程流水线（P7）。

生成阶段（默认 08:00，run_date 决定周期）：
    PENDING → 取数(messages.json) → DATA_READY → 排行(ranking.json/txt)
    → RANKING_READY → DeepSeek(image_prompt.txt) → PROMPT_READY
    → Codex 串行生图(daily_image.png) → IMAGE_READY → READY_TO_SEND
发送阶段（每群 send_time）：
    READY_TO_SEND/IMAGE_READY → 发排行榜文字 → 发图片 → SENT

约束：
- 每个群独立状态；某群失败不阻塞其他群；
- 生图阶段使用全局单队列严格串行；
- 同一群同一统计周期已到终态则跳过（force 可重跑）；
- SENT 绝不重复发送（force_send 允许重发内容）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.ai.prompt_builder import DeepSeekImagePromptBuilder
from app.ai.prompt_builder_types import PromptInput
from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.data_sources.base import WeChatDataSource
from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource
from app.db import repository as repo
from app.db.models import Group
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import ImageJob, SerialImageQueue
from app.ranking.engine import RankingEngine
from app.ranking.renderer import RankingRenderer
from app.scheduler.period import PeriodResolver
from app.sender.base import WechatSender
from app.sender.wechat_automation import WechatAutomationSender
from app.v2.constants import (
    DATA_READY,
    FAILED,
    IMAGE_GENERATION_FAILED,
    IMAGE_READY,
    IMAGE_FILE_MISSING,
    MESSAGE_FETCH_FAILED,
    PENDING,
    PROMPT_FAILED,
    PROMPT_READY,
    RANKING_FAILED,
    RANKING_READY,
    READY_TO_SEND,
    SENT,
    WECHAT_DATA_UNAVAILABLE,
)
from app.v2.run_store import RunStore

logger = get_logger("groupbrief.pipeline")


class DailyPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        data_source: WeChatDataSource | None = None,
        ranking_engine: RankingEngine | None = None,
        renderer: RankingRenderer | None = None,
        prompt_builder: DeepSeekImagePromptBuilder | None = None,
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
        self.prompt_builder = prompt_builder or DeepSeekImagePromptBuilder(self.settings)
        self.image_generator = image_generator or CodexImageGenerator(self.settings)
        self.sender = sender or WechatAutomationSender(settings=self.settings, dry_run=dry_run)
        self.store = store or RunStore(self.settings.output_dir)
        self.dry_run = dry_run

    # ================= 生成阶段 =================

    def generate_all(
        self,
        run_date: str | None = None,
        group_ids: list[int] | None = None,
        force: bool = False,
    ) -> list[dict]:
        window = self.period_resolver.resolve(run_date=parse_date(run_date), timezone=self.settings.app_timezone)
        if not window.should_run:
            weekday_name = {5: "周六", 6: "周日"}.get(window.weekday, str(window.weekday))
            logger.info("%s 不生成", weekday_name)
            return [{"status": "skipped", "reason": "周六/周日不生成"}]

        run_date_str = window.run_date.isoformat()
        groups = self._load_groups(group_ids)
        if not groups:
            return [{"status": "no_groups", "reason": "无启用群"}]

        results: list[dict] = []
        image_jobs: list[ImageJob] = []
        for group in groups:
            result = self._generate_one(group, window, run_date_str, force)
            results.append(result)
            if result.get("need_image"):
                image_jobs.append(
                    self._make_image_job(group, run_date_str, force)
                )

        # 全局单队列串行生图
        if image_jobs:
            logger.info("进入生图阶段：%d 个群串行生成", len(image_jobs))
            queue = SerialImageQueue(run_hook=self._image_hook)
            queue.run_all(image_jobs)
            for job in image_jobs:
                self._after_image(job, run_date_str)

        return results

    def _generate_one(self, group: Group, window, run_date: str, force: bool) -> dict:
        group_name = group.display_name or group.wechat_group_name
        store = self.store

        # 防重复：同一群同一周期已到终态
        run = store.load_run(group_name, run_date)
        if not force and run.get("status") in (IMAGE_READY, READY_TO_SEND, SENT):
            logger.info("群 %s %s 已到 %s，跳过生成", group_name, run_date, run.get("status"))
            return {"group_name": group_name, "status": "skipped", "detail": f"已{run.get('status')}"}

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
            "provider": self.data_source.name,
            "failed_stage": None,
            "error": None,
        }
        store.update(group_name, run_date, status=PENDING, **base)

        # ---- 1) 数据读取 ----
        if not group.wechat_group_id:
            store.update(group_name, run_date, status=FAILED, failed_stage="data", error="群未绑定微信群 ID")
            return {"group_name": group_name, "status": "failed", "error_type": WECHAT_DATA_UNAVAILABLE}
        fetch = self.data_source.fetch_messages(
            group.wechat_group_id, window.period_start, window.period_end
        )
        if fetch.status.value != "OK" or not fetch.messages:
            error_type = fetch.error_type or MESSAGE_FETCH_FAILED
            store.update(group_name, run_date, status=FAILED, failed_stage="data",
                         error=fetch.detail or fetch.status.value, error_type=error_type)
            return {"group_name": group_name, "status": "failed", "error_type": error_type,
                    "detail": fetch.detail}
        self._save_json(store.messages_path(group_name, run_date), [m.to_dict() for m in fetch.messages])
        store.update(group_name, run_date, status=DATA_READY, message_count=len(fetch.messages))

        # ---- 2) 排行榜 ----
        try:
            ranking = self.ranking_engine.compute(
                fetch.messages, group_name, period_start, period_end
            )
        except Exception as e:
            store.update(group_name, run_date, status=FAILED, failed_stage="ranking", error=str(e)[:300])
            return {"group_name": group_name, "status": "failed", "error_type": RANKING_FAILED}
        self._save_json(store.ranking_json_path(group_name, run_date), ranking.to_dict())
        ranking_txt = self.renderer.render(ranking, template_name=group.ranking_template)
        store.ranking_txt_path(group_name, run_date).write_text(ranking_txt, encoding="utf-8")
        store.update(group_name, run_date, status=RANKING_READY,
                     speaker_count=ranking.speaker_count, message_count=ranking.message_count)

        # ---- 3) 生图 Prompt（DeepSeek）----
        prompt_msgs = [m for m in fetch.messages if RankingEngine._countable(m)]
        prompt_input = PromptInput(
            group_name=group_name,
            period_start=period_start,
            period_end=period_end,
            message_count=ranking.message_count,
            speaker_count=ranking.speaker_count,
            messages=prompt_msgs,
            template=group.image_prompt_template,
        )
        prompt_out = self.prompt_builder.build(prompt_input)
        if not prompt_out.success:
            store.update(group_name, run_date, status=FAILED, failed_stage="prompt",
                         error=prompt_out.error, error_type=PROMPT_FAILED)
            return {"group_name": group_name, "status": "failed", "error_type": PROMPT_FAILED,
                    "detail": prompt_out.error}
        store.prompt_path(group_name, run_date).write_text(prompt_out.prompt, encoding="utf-8")
        store.update(group_name, run_date, status=PROMPT_READY, prompt_meta=prompt_out.meta)

        # ---- 4) 生图判断 ----
        if not group.image_enabled:
            store.update(group_name, run_date, status=READY_TO_SEND)
            return {"group_name": group_name, "status": "ready_to_send", "detail": "未启用生图"}
        return {"group_name": group_name, "status": "prompt_ready", "need_image": True}

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
        self.store.update(
            job.group_name, job.output_path.parent.name,
            status=status,
            image_error=result.get("detail") if not result["success"] else None,
            image_status=result["status"],
            error_type=error_type if not result["success"] else None,
        )

    def _after_image(self, job: ImageJob, run_date: str) -> None:
        run = self.store.load_run(job.group_name, run_date)
        if run.get("status") == IMAGE_READY:
            self.store.update(job.group_name, run_date, status=READY_TO_SEND)

    # ================= 发送阶段 =================

    def send_due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        run_date = now.date().isoformat()
        results: list[dict] = []
        for group in self._load_groups():
            group_name = group.display_name or group.wechat_group_name
            run = self.store.load_run(group_name, run_date)
            status = run.get("status")
            if status not in (IMAGE_READY, READY_TO_SEND):
                continue
            if run.get("sent_at"):
                continue  # 已发送，绝不重复
            send_time = parse_send_time(group.send_time or run.get("send_time", "08:30"))
            if now.time() < send_time:
                continue  # 未到发送时间
            result = self._send_one(group, group_name, run, run_date, now)
            results.append(result)
        return results

    def _send_one(self, group: Group, group_name: str, run: dict, run_date: str, now: datetime) -> dict:
        target = group.send_target or group.wechat_group_name or group_name
        ranking_txt = self.store.ranking_txt_path(group_name, run_date)
        image = self.store.image_path(group_name, run_date)

        if not ranking_txt.exists():
            self.store.update(group_name, run_date, status=FAILED, failed_stage="send",
                              error="ranking.txt 缺失", error_type="SEND_TEXT_FAILED")
            return {"group_name": group_name, "status": "failed", "error_type": "SEND_TEXT_FAILED"}

        # 第一条：排行榜文字
        text_result = self.sender.send_text(target, ranking_txt.read_text(encoding="utf-8"))
        if not text_result.success:
            self.store.update(group_name, run_date, status=FAILED, failed_stage="send",
                              error=text_result.detail, error_type="SEND_TEXT_FAILED")
            return {"group_name": group_name, "status": "failed", "error_type": "SEND_TEXT_FAILED",
                    "detail": text_result.detail}

        # 第二条：图片（dry_run 或 image_enabled=false 时跳过图片）
        image_sent = True
        if group.image_enabled:
            if not image.exists():
                self.store.update(group_name, run_date, status=FAILED, failed_stage="send",
                                  error="daily_image.png 缺失", error_type=IMAGE_FILE_MISSING)
                return {"group_name": group_name, "status": "failed", "error_type": IMAGE_FILE_MISSING,
                        "detail": "文字已发送但图片缺失"}
            image_result = self.sender.send_image(target, str(image.resolve()))
            image_sent = image_result.success
            if not image_sent:
                self.store.update(group_name, run_date, status=FAILED, failed_stage="send",
                                  error=image_result.detail, error_type="SEND_IMAGE_FAILED")
                return {"group_name": group_name, "status": "failed", "error_type": "SEND_IMAGE_FAILED",
                        "detail": image_result.detail}

        self.store.update(group_name, run_date, status=SENT,
                          sent_at=now.strftime("%Y-%m-%d %H:%M:%S"), sent_target=target)
        logger.info("群 %s 已发送（文字+图片）→ SENT", group_name)
        return {"group_name": group_name, "status": "sent", "sent_at": now.isoformat()}

    # ================= 手动操作 =================

    def force_generate(self, group_id: int, run_date: str | None = None) -> dict:
        run_date = run_date or datetime.now().date().isoformat()
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "error": f"群不存在 {group_id}"}
        window = self.period_resolver.resolve(run_date=parse_date(run_date), timezone=self.settings.app_timezone)
        if not window.should_run:
            return {"status": "skipped", "reason": "该日期不生成"}
        return self._generate_one(group, window, run_date, force=True)

    def force_send(self, group_id: int, run_date: str | None = None) -> dict:
        run_date = run_date or datetime.now().date().isoformat()
        group = self._get_group(group_id)
        if not group:
            return {"status": "failed", "error": f"群不存在 {group_id}"}
        group_name = group.display_name or group.wechat_group_name
        run = self.store.load_run(group_name, run_date)
        if run.get("status") not in (IMAGE_READY, READY_TO_SEND):
            return {"status": "failed", "error": f"状态 {run.get('status')} 不可发送"}
        return self._send_one(group, group_name, run, run_date, datetime.now())

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
            return repo.get_group(session, group_id)

    def _save_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_send_time(value: str) -> time:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return time(8, 30)
