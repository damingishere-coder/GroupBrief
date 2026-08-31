"""DailyPipeline 的微信发送阶段实现。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.core.observability import log_event
from app.db.models import Group
from app.image.image_task import verify_image
from app.pipeline.stage_result import StageResult
from app.sender.base import WechatSender
from app.services.group_name_sync import effective_send_target, send_target_mode
from app.v2.constants import FAILED, IMAGE_FILE_MISSING, READY_TO_SEND, SENT
from app.v2.run_store import RunStore


@dataclass
class DeliveryContext:
    group: Group
    group_name: str
    run: dict
    run_date: str
    now: datetime
    allow_hold: bool
    allow_sent: bool
    target: str
    claim_id: str = ""
    ranking_text: str = ""
    image_enabled: bool = False
    image_path: str | None = None
    text_sha256: str = ""
    image_sha256: str = ""
    text_sent_at: str = ""
    image_sent_at: str = ""
    verification_levels: list[str] = field(default_factory=list)


class DeliveryStages:
    """按 claim、预检、文字、图片、终态顺序执行微信发送。"""

    def __init__(
        self,
        *,
        settings: Settings,
        sender: WechatSender,
        store: RunStore,
        name_sync_audit,
        logger,
    ) -> None:
        self.settings = settings
        self.sender = sender
        self.store = store
        self._name_sync_audit = name_sync_audit
        self.logger = logger

    def run(
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
        context = DeliveryContext(
            group=group,
            group_name=group_name,
            run=run,
            run_date=run_date,
            now=now,
            allow_hold=allow_hold,
            allow_sent=allow_sent,
            target=effective_send_target(group),
        )

        claimed = self._claim(context)
        if claimed.is_terminal:
            return claimed.terminal_response()
        context = claimed.next_value()

        prepared = self._prepare_payload(context)
        if prepared.is_terminal:
            return prepared.terminal_response()
        context = prepared.next_value()

        text_stage = self._send_text(context)
        if text_stage.is_terminal:
            return text_stage.terminal_response()
        context = text_stage.next_value()

        image_stage = self._send_image(context)
        if image_stage.is_terminal:
            return image_stage.terminal_response()
        return self._complete(image_stage.next_value())

    def _claim(self, context: DeliveryContext) -> StageResult[DeliveryContext]:
        group = context.group
        self.store.update(
            context.group_name,
            context.run_date,
            wechat_group_name=str(group.wechat_group_name or "").strip(),
            effective_send_target=context.target,
            send_target_mode=send_target_mode(group),
            **self._name_sync_audit(group),
        )
        claim_id, run, claim_reason = self.store.claim_send(
            context.group_name,
            context.run_date,
            now=context.now,
            lease_seconds=self.settings.wechat_send_claim_seconds,
            allow_hold=context.allow_hold,
            allow_sent=context.allow_sent,
        )
        if not claim_id:
            if claim_reason == "result_unknown":
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "held",
                        "error_type": "SEND_RESULT_UNKNOWN",
                        "detail": "上次发送结果未知，已暂停自动重试",
                    }
                )
            if claim_reason == "failed_final":
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "failed_final",
                        "error_type": "SEND_RETRY_EXHAUSTED",
                        "detail": "发送重试预算已耗尽，已暂停自动重试",
                    }
                )
            if claim_reason == "retry_not_due":
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "retry_scheduled",
                        "detail": "发送重试尚未到期",
                        "next_retry_at": run.get("send_next_retry_at"),
                    }
                )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "skipped",
                    "detail": f"发送任务未领取：{claim_reason}",
                }
            )
        context.claim_id = claim_id
        context.run = run
        return StageResult.proceed(context)

    def _prepare_payload(
        self,
        context: DeliveryContext,
    ) -> StageResult[DeliveryContext]:
        ranking_path = self.store.ranking_txt_path(
            context.group_name,
            context.run_date,
        )
        image_path = self.store.image_path(context.group_name, context.run_date)
        try:
            ranking_text = ranking_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            self.store.finish_send_claim(
                context.group_name,
                context.run_date,
                context.claim_id,
                send_state="failed",
                status=FAILED,
                failed_stage="send",
                error="ranking.txt 缺失或无法读取",
                error_type="SEND_TEXT_FAILED",
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": "SEND_TEXT_FAILED",
                    "detail": "ranking.txt 缺失或无法读取",
                }
            )
        if not ranking_text.strip():
            self.store.finish_send_claim(
                context.group_name,
                context.run_date,
                context.claim_id,
                send_state="failed",
                status=FAILED,
                failed_stage="send",
                error="ranking.txt 为空",
                error_type="SEND_TEXT_FAILED",
            )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed",
                    "error_type": "SEND_TEXT_FAILED",
                    "detail": "ranking.txt 为空",
                }
            )

        context.ranking_text = ranking_text
        context.text_sha256 = hashlib.sha256(ranking_text.encode("utf-8")).hexdigest()
        context.image_enabled = bool(context.group.image_enabled)
        if context.image_enabled:
            image_ok, image_detail = verify_image(image_path)
            if not image_ok:
                self.store.finish_send_claim(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    send_state="failed",
                    status=FAILED,
                    failed_stage="send",
                    error=image_detail,
                    error_type=IMAGE_FILE_MISSING,
                )
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "failed",
                        "error_type": IMAGE_FILE_MISSING,
                        "detail": image_detail,
                    }
                )
            context.image_path = str(image_path.resolve())
            try:
                context.image_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
            except OSError:
                self.store.finish_send_claim(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    send_state="failed",
                    status=FAILED,
                    failed_stage="send",
                    error="图片无法读取以生成送达证据",
                    error_type=IMAGE_FILE_MISSING,
                )
                return StageResult.stop(
                    {
                        "group_name": context.group_name,
                        "status": "failed",
                        "error_type": IMAGE_FILE_MISSING,
                        "detail": "图片无法读取以生成送达证据",
                    }
                )
        evidence = {
            "target": context.target,
            "prepared_at": datetime.now(context.now.tzinfo).isoformat(),
            "text_sha256": context.text_sha256,
            "image_sha256": context.image_sha256,
            "image_enabled": context.image_enabled,
            "result": "pending",
        }
        persisted, latest = self.store.update_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            delivery_evidence=evidence,
        )
        if not persisted:
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "skipped",
                    "error_type": "SEND_CLAIM_LOST",
                    "detail": "送达证据落盘前发送 claim 已失效，未调用发送器",
                }
            )
        context.run = latest
        context.text_sent_at = str(context.run.get("text_sent_at") or "")
        context.image_sent_at = str(context.run.get("image_sent_at") or "")
        return StageResult.proceed(context)

    def _send_text(
        self,
        context: DeliveryContext,
    ) -> StageResult[DeliveryContext]:
        if context.text_sent_at:
            level = context.run.get("text_verification_level")
            if level:
                context.verification_levels.append(str(level))
            return StageResult.proceed(context)

        started_at = datetime.now(context.now.tzinfo).isoformat()
        updated, _ = self.store.update_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            send_state="sending_text",
            text_attempt_started_at=started_at,
            text_attempt_finished_at="",
            text_submitted_at="",
            text_verified_at="",
        )
        if not updated:
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "skipped",
                    "detail": "发送 claim 已失效",
                }
            )
        try:
            result = self.sender.send_text(context.target, context.ranking_text)
        except Exception as exc:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "text",
                    f"文字发送异常：{exc}",
                )
            )
        finished_at = datetime.now(context.now.tzinfo).isoformat()
        if result.outcome_unknown:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "text",
                    result.detail,
                    submitted_at=finished_at if result.submitted else "",
                    diagnostics=result.diagnostics,
                )
            )
        if not result.success:
            if result.submitted:
                return StageResult.stop(
                    self.finish_unknown(
                        context.group_name,
                        context.run_date,
                        context.claim_id,
                        "text",
                        result.detail or "文字已提交，但发送结果未确认",
                        submitted_at=finished_at,
                        diagnostics=result.diagnostics,
                    )
                )
            persisted, failed_run, final = self.store.finish_send_failure(
                context.group_name,
                context.run_date,
                context.claim_id,
                stage="text",
                error_type="SEND_TEXT_FAILED",
                detail=result.detail,
                now=context.now,
                diagnostics=result.diagnostics,
                status=context.run.get("status", READY_TO_SEND),
                text_attempt_finished_at=finished_at,
                text_submitted_at="",
                text_verification_diagnostics=result.diagnostics,
                delivery_evidence=self._delivery_evidence(
                    context,
                    result="failed",
                    completed_at=finished_at,
                    detail=result.detail,
                ),
            )
            if not persisted:
                return StageResult.stop(
                    self.finish_unknown(
                        context.group_name,
                        context.run_date,
                        context.claim_id,
                        "text",
                        f"文字发送失败状态无法持久化：{result.detail}",
                        diagnostics=result.diagnostics,
                    )
                )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed_final" if final else "retry_scheduled",
                    "error_type": "SEND_TEXT_FAILED",
                    "detail": result.detail,
                    "next_retry_at": failed_run.get("send_next_retry_at"),
                }
            )

        context.text_sent_at = result.sent_at or finished_at
        level = result.verification_level or "provider_reported"
        context.verification_levels.append(level)
        persisted, _ = self.store.update_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            send_state="text_verified",
            text_attempt_finished_at=finished_at,
            text_submitted_at=(
                finished_at if result.submitted or result.success else ""
            ),
            text_verified_at=finished_at,
            text_sent_at=context.text_sent_at,
            text_verification_level=level,
            text_verification_diagnostics=result.diagnostics,
            send_error="",
            send_error_type="",
        )
        if not persisted:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "text",
                    "文字发送已成功，但成功状态无法持久化",
                    submitted_at=finished_at,
                    diagnostics=result.diagnostics,
                )
            )
        return StageResult.proceed(context)

    def _send_image(
        self,
        context: DeliveryContext,
    ) -> StageResult[DeliveryContext]:
        if not context.image_enabled:
            return StageResult.proceed(context)

        started_at = datetime.now(context.now.tzinfo).isoformat()
        updated, _ = self.store.update_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            send_state="sending_image",
            image_attempt_started_at=started_at,
            image_attempt_finished_at="",
            image_submitted_at="",
            image_verified_at="",
        )
        if not updated:
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "skipped",
                    "error_type": "SEND_CLAIM_LOST",
                    "detail": "图片提交前发送 claim 已失效，未调用发送器",
                }
            )
        try:
            result = self.sender.send_image(context.target, context.image_path)
        except Exception as exc:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "image",
                    f"图片发送异常：{exc}",
                )
            )
        finished_at = datetime.now(context.now.tzinfo).isoformat()
        if result.outcome_unknown:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "image",
                    result.detail,
                    submitted_at=finished_at if result.submitted else "",
                    diagnostics=result.diagnostics,
                )
            )
        if not result.success:
            if result.submitted:
                return StageResult.stop(
                    self.finish_unknown(
                        context.group_name,
                        context.run_date,
                        context.claim_id,
                        "image",
                        result.detail or "图片已提交，但发送结果未确认",
                        submitted_at=finished_at,
                        diagnostics=result.diagnostics,
                    )
                )
            persisted, failed_run, final = self.store.finish_send_failure(
                context.group_name,
                context.run_date,
                context.claim_id,
                stage="image",
                error_type="SEND_IMAGE_FAILED",
                detail=result.detail,
                now=context.now,
                diagnostics=result.diagnostics,
                status=context.run.get("status", READY_TO_SEND),
                text_sent_at=context.text_sent_at,
                image_attempt_finished_at=finished_at,
                image_submitted_at="",
                image_verification_diagnostics=result.diagnostics,
                delivery_evidence=self._delivery_evidence(
                    context,
                    result="failed",
                    completed_at=finished_at,
                    detail=result.detail,
                ),
            )
            if not persisted:
                return StageResult.stop(
                    self.finish_unknown(
                        context.group_name,
                        context.run_date,
                        context.claim_id,
                        "image",
                        f"图片发送失败状态无法持久化：{result.detail}",
                        diagnostics=result.diagnostics,
                    )
                )
            return StageResult.stop(
                {
                    "group_name": context.group_name,
                    "status": "failed_final" if final else "retry_scheduled",
                    "error_type": "SEND_IMAGE_FAILED",
                    "detail": result.detail,
                    "next_retry_at": failed_run.get("send_next_retry_at"),
                }
            )

        context.image_sent_at = result.sent_at or finished_at
        level = result.verification_level or "provider_reported"
        context.verification_levels.append(level)
        persisted, _ = self.store.update_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            send_state="image_verified",
            image_attempt_finished_at=finished_at,
            image_submitted_at=(
                finished_at if result.submitted or result.success else ""
            ),
            image_verified_at=finished_at,
            image_sent_at=context.image_sent_at,
            image_verification_level=level,
            image_verification_diagnostics=result.diagnostics,
            send_error="",
            send_error_type="",
            send_next_retry_at="",
        )
        if not persisted:
            return StageResult.stop(
                self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "image",
                    "图片发送已成功，但成功状态无法持久化",
                    submitted_at=finished_at,
                    diagnostics=result.diagnostics,
                )
            )
        return StageResult.proceed(context)

    def _complete(self, context: DeliveryContext) -> dict:
        levels = context.verification_levels
        if levels and all(level == "ui_observed" for level in levels):
            verification_level = "ui_observed"
        elif levels and all(level == "dry_run" for level in levels):
            verification_level = "dry_run"
        elif "manual_ui_observed" in levels:
            verification_level = "manual_ui_observed"
        else:
            verification_level = "provider_reported"
        persisted, run = self.store.finish_send_claim(
            context.group_name,
            context.run_date,
            context.claim_id,
            send_state="sent",
            status=SENT,
            sent_at=context.now.isoformat(),
            sent_target=context.target,
            text_sent_at=context.text_sent_at,
            image_sent_at=context.image_sent_at,
            send_error="",
            send_error_type="",
            verification_level=verification_level,
            delivery_evidence=self._delivery_evidence(
                context,
                result="sent",
                completed_at=context.now.isoformat(),
                verification_level=verification_level,
            ),
            send_hold=False,
            send_hold_reason="",
            needs_manual_send=False,
            image_regen_status=(
                "sent"
                if context.run.get("image_regen_status") == "ready_for_review"
                else context.run.get("image_regen_status")
            ),
        )
        if not persisted:
            if run.get("status") == SENT or run.get("sent_at"):
                persisted = True
            else:
                return self.finish_unknown(
                    context.group_name,
                    context.run_date,
                    context.claim_id,
                    "finalize",
                    "微信发送已完成，但 SENT 终态无法持久化",
                )
        if context.image_enabled:
            self.logger.info("群 %s 已发送（文字+图片）→ SENT", context.group_name)
            detail = "文字和图片已发送"
        else:
            self.logger.info("群 %s 已发送（仅文字，未启用图片）→ SENT", context.group_name)
            detail = "文字已发送（未启用图片）"
        latest = self.store.load_run(context.group_name, context.run_date)
        log_event(
            self.logger,
            "WECHAT_SEND_FINISHED",
            group_task_id=latest.get("group_task_id"),
            group_name=context.group_name,
            run_date=context.run_date,
            stage="SEND",
            status="sent",
            attempt=latest.get("retry_attempt_count", 0),
        )
        return {
            "group_name": context.group_name,
            "status": "sent",
            "detail": detail,
            "sent_at": context.now.isoformat(),
        }

    @staticmethod
    def _delivery_evidence(
        context: DeliveryContext,
        *,
        result: str,
        completed_at: str,
        detail: str = "",
        verification_level: str = "",
    ) -> dict:
        evidence = dict(
            context.run.get("delivery_evidence")
            if isinstance(context.run.get("delivery_evidence"), dict)
            else {}
        )
        evidence.update(
            target=context.target,
            completed_at=completed_at,
            text_sha256=context.text_sha256,
            image_sha256=context.image_sha256,
            image_enabled=context.image_enabled,
            result=result,
            detail=str(detail or "")[:300],
            verification_level=verification_level,
        )
        return evidence

    def finish_unknown(
        self,
        group_name: str,
        run_date: str,
        claim_id: str,
        stage: str,
        detail: str,
        *,
        submitted_at: str = "",
        diagnostics: dict[str, object] | None = None,
    ) -> dict:
        finished_at = datetime.now(
            ZoneInfo(self.settings.app_timezone)
        ).isoformat()
        current = self.store.load_run(group_name, run_date)
        evidence = dict(
            current.get("delivery_evidence")
            if isinstance(current.get("delivery_evidence"), dict)
            else {}
        )
        evidence.update(
            completed_at=finished_at,
            result="unknown",
            detail=str(detail or "")[:300],
            verification_level="unknown",
        )
        fields = {
            f"{stage}_attempt_finished_at": "",
            f"{stage}_submitted_at": submitted_at,
            f"{stage}_verification_diagnostics": diagnostics or {},
            "delivery_evidence": evidence,
        }
        persisted, run = self.store.finish_send_claim(
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
            send_unknown_stage=stage,
            **fields,
        )
        if not persisted:
            persisted, run, reason = self.store.mark_send_result_unknown(
                group_name,
                run_date,
                claim_id,
                stage=stage,
                detail=detail,
                submitted_at=submitted_at,
                diagnostics=diagnostics,
                now=datetime.now(ZoneInfo(self.settings.app_timezone)),
            )
            if not persisted:
                self.logger.error(
                    "发送结果未知且状态无法持久化 group=%s date=%s stage=%s reason=%s",
                    group_name,
                    run_date,
                    stage,
                    reason,
                )
                detail = f"{detail}；且 unknown 状态持久化失败（{reason}）"
        log_event(
            self.logger,
            "WECHAT_SEND_UNKNOWN",
            group_name=group_name,
            run_date=run_date,
            stage=stage,
            status="held",
            error_type="SEND_RESULT_UNKNOWN",
            error_summary=detail,
        )
        return {
            "group_name": group_name,
            "status": "held",
            "error_type": "SEND_RESULT_UNKNOWN",
            "detail": detail,
        }
