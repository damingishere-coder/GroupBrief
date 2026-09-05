"""上一自然周洞察：确定性聚合、一次 AI 叙述、本地卡片和独立发送。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import Group
from app.image.fallback import _fit_lines, _load_font
from app.image.image_task import verify_image
from app.providers.ai.base import ExternalCallResultUnknownError
from app.providers.ai.codex import build_summary_provider
from app.sender.base import WechatSender
from app.sender.wechat_native import create_wechat_sender
from app.services.generation_runtime import generation_mutex
from app.services.group_name_sync import effective_send_target
from app.services.group_provider_config import resolve_group_ai_settings
from app.v2.run_store import RunStore
from app.weekly.store import WeeklyStore
from app.repair.store import RepairIncidentStore


def previous_natural_week(reference: date) -> tuple[date, date]:
    current_monday = reference - timedelta(days=reference.weekday())
    end = current_monday - timedelta(days=1)
    return end - timedelta(days=6), end


def _safe_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _nonnegative_int(value: object, field: str) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 不是合法整数") from exc
    if parsed < 0:
        raise ValueError(f"{field} 不能为负数")
    return parsed


def _failure_fields(error_type: str, stage: str, detail: str) -> dict:
    summary = str(detail)[:300]
    return {
        "error_type": error_type,
        "stage": stage,
        "error_summary": summary,
        "failure_fingerprint": hashlib.sha256(
            f"weekly|{error_type}|{stage}|{summary}".encode("utf-8")
        ).hexdigest(),
        "retryable": False,
    }


class WeeklyInsightsService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        daily_store: RunStore | None = None,
        weekly_store: WeeklyStore | None = None,
        provider_factory: Callable[[Settings], object] | None = None,
        sender: WechatSender | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.daily_store = daily_store or RunStore(self.settings.output_dir)
        self.store = weekly_store or WeeklyStore(self.daily_store.root)
        self.provider_factory = provider_factory or build_summary_provider
        self.sender = sender or create_wechat_sender(settings=self.settings)

    def generate_previous_week(
        self,
        *,
        now: datetime | None = None,
        group_ids: list[int] | None = None,
        acquire_lock: bool = True,
    ) -> dict:
        tz = ZoneInfo(self.settings.app_timezone)
        now = now or datetime.now(tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        if acquire_lock:
            with generation_mutex():
                return self.generate_previous_week(
                    now=now,
                    group_ids=group_ids,
                    acquire_lock=False,
                )
        start, end = previous_natural_week(now.date())
        groups = self._groups(group_ids)
        results: list[dict] = []
        for group in groups:
            try:
                results.append(self._generate_group(group, start, end, now))
            except Exception as exc:
                assert group.id is not None
                error_type = (
                    "WEEKLY_DATA_INVALID"
                    if isinstance(exc, (TypeError, ValueError))
                    else "WEEKLY_ARTIFACT_WRITE_FAILED"
                )
                detail = str(exc)[:300]
                fingerprint = hashlib.sha256(
                    f"weekly|{error_type}|{detail}".encode("utf-8")
                ).hexdigest()
                self.store.save(
                    start.isoformat(),
                    end.isoformat(),
                    group.id,
                    {
                        "status": "needs_attention",
                        "group_name": group.display_name,
                        "error_type": error_type,
                        "stage": "aggregate" if error_type == "WEEKLY_DATA_INVALID" else "artifact",
                        "error_summary": detail,
                        "failure_fingerprint": fingerprint,
                        "retryable": False,
                        "generated_at": now.isoformat(),
                    },
                )
                RepairIncidentStore(self.settings).record(
                    scope="weekly",
                    error_type=error_type,
                    stage="aggregate" if error_type == "WEEKLY_DATA_INVALID" else "artifact",
                    source_path=f".weekly/{start.isoformat()}_{end.isoformat()}/group-{group.id}/weekly.json",
                    error_summary=detail,
                    now=now,
                )
                results.append(
                    {
                        "group_id": group.id,
                        "group_name": group.display_name,
                        "status": "held",
                        "error_type": error_type,
                        "detail": detail,
                    }
                )
        return {
            "status": "complete" if all(item["status"] in {"ready_to_send", "skipped"} for item in results) else "partial",
            "week_start": start.isoformat(),
            "week_end": end.isoformat(),
            "results": results,
        }

    def _groups(self, group_ids: list[int] | None = None) -> list[Group]:
        repo.init_db(self.settings)
        with Session(repo.engine) as session:
            groups = repo.list_groups(session, only_enabled=True)
        if group_ids is not None:
            wanted = {int(value) for value in group_ids}
            groups = [group for group in groups if group.id in wanted]
        return groups

    def _generate_group(
        self,
        group: Group,
        week_start: date,
        week_end: date,
        now: datetime,
    ) -> dict:
        assert group.id is not None
        start_text, end_text = week_start.isoformat(), week_end.isoformat()
        existing = self.store.load(start_text, end_text, group.id)
        if existing.get("status") in {"ready_to_send", "sent", "needs_attention"}:
            return {
                "group_id": group.id,
                "group_name": group.display_name,
                "status": "skipped",
                "detail": f"周报已处于 {existing.get('status')}",
            }
        if existing.get("status") in {"building", "sending"}:
            # 外部调用或发送中断无法证明是否已提交；重新执行可能产生重复调用。
            error_type = (
                "WEEKLY_AI_RESULT_UNKNOWN"
                if existing.get("status") == "building"
                else "WEEKLY_SEND_RESULT_UNKNOWN"
            )
            self.store.update(
                start_text,
                end_text,
                group.id,
                status="needs_attention",
                **_failure_fields(error_type, "external_call", "上次周报外部操作中断"),
            )
            return {
                "group_id": group.id,
                "group_name": group.display_name,
                "status": "skipped",
                "detail": "上次周报外部操作中断，已转人工复核",
            }

        aggregate = self._aggregate(group, week_start, week_end)
        deterministic = self._deterministic_narrative(group, aggregate, start_text, end_text)
        ai_status = "not_attempted"
        ai_error = ""
        narrative = deterministic
        actual_provider = "local_deterministic"
        actual_model = "none"
        ai_call_count = 0
        requested_provider = ""
        requested_model = ""
        try:
            provider_settings, provider_meta = resolve_group_ai_settings(
                self.settings,
                group,
                capability="summary",
            )
            # 周报每群最多一次外部调用；不在同一周报里追加 Provider fallback 调用。
            provider_settings = provider_settings.model_copy(update={"summary_provider_fallback": ""})
            requested_provider = provider_meta["provider"]
            requested_model = provider_meta["model"]
            provider = self.provider_factory(provider_settings)
            ai_call_count = 1
            self.store.save(
                start_text,
                end_text,
                group.id,
                {
                    "status": "building",
                    "group_name": group.display_name,
                    "ai_attempt_started_at": now.isoformat(),
                    "ai_call_count": 1,
                    "aggregation": aggregate,
                },
            )
            narrative = str(
                provider._chat(
                    [
                        {
                            "role": "system",
                            "content": "你只根据聚合统计写一段简洁中文周度洞察，不补充原始聊天或不存在的事实。",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")),
                        },
                    ],
                    response_format="text",
                    temperature=0.2,
                    max_tokens=1200,
                )
            ).strip()
            if not narrative:
                raise ValueError("周报 AI 返回空文本")
            ai_status = "completed"
            actual_provider = str(
                getattr(provider, "last_provider_used", "")
                or getattr(provider, "name", requested_provider)
            )
            actual_model = str(getattr(provider, "model", requested_model))
        except ExternalCallResultUnknownError as exc:
            ai_status = "result_unknown"
            ai_error = str(exc)[:300]
            narrative = deterministic
        except Exception as exc:
            ai_status = "failed"
            ai_error = str(exc)[:300]
            narrative = deterministic

        text_path = self.store.text_path(start_text, end_text, group.id)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(narrative, encoding="utf-8")
        card_path = self.store.card_path(start_text, end_text, group.id)
        self._render_card(group, aggregate, start_text, end_text, card_path)
        text_bytes = narrative.encode("utf-8")
        card_bytes = card_path.read_bytes()
        result_unknown = ai_status == "result_unknown"
        error_type = (
            "WEEKLY_AI_RESULT_UNKNOWN"
            if result_unknown
            else "WEEKLY_AI_FAILED_FALLBACK"
            if ai_status == "failed"
            else ""
        )
        payload = self.store.save(
            start_text,
            end_text,
            group.id,
            {
                "status": "needs_attention" if result_unknown else "ready_to_send",
                "group_name": group.display_name,
                "wechat_group_id": group.wechat_group_id,
                "send_target_snapshot": effective_send_target(group),
                "aggregation": aggregate,
                "narrative": narrative,
                "narrative_source": "ai" if ai_status == "completed" else "local_deterministic",
                "ai_status": ai_status,
                "ai_error": ai_error,
                "ai_call_count": ai_call_count,
                "error_type": error_type,
                "stage": "ai" if error_type else "complete",
                "retryable": False,
                "failure_fingerprint": (
                    hashlib.sha256(f"weekly|{error_type}|{ai_error}".encode("utf-8")).hexdigest()
                    if error_type else ""
                ),
                "requested_provider": requested_provider,
                "requested_model": requested_model,
                "actual_provider": actual_provider,
                "actual_model": actual_model,
                "text_sha256": _sha256_bytes(text_bytes),
                "card_sha256": _sha256_bytes(card_bytes),
                "generated_at": now.isoformat(),
            },
        )
        return {
            "group_id": group.id,
            "group_name": group.display_name,
            "status": payload["status"],
            "ai_status": ai_status,
            "missing_days": aggregate["missing_days"],
        }

    def _aggregate(self, group: Group, week_start: date, week_end: date) -> dict:
        contributors: dict[str, dict] = {}
        topics: Counter[str] = Counter()
        daily: list[dict] = []
        missing_days: list[str] = []
        current = week_start
        while current <= week_end:
            run_date = current.isoformat()
            run = self.daily_store.load_run(group.display_name, run_date)
            ranking = _safe_json(self.daily_store.ranking_json_path(group.display_name, run_date))
            if not ranking:
                missing_days.append(run_date)
            message_count = _nonnegative_int(
                ranking.get("message_count") or run.get("message_count") or 0,
                f"{run_date}.message_count",
            )
            speaker_count = _nonnegative_int(
                ranking.get("speaker_count") or run.get("speaker_count") or 0,
                f"{run_date}.speaker_count",
            )
            daily.append(
                {
                    "date": run_date,
                    "message_count": message_count,
                    "speaker_count": speaker_count,
                    "status": str(run.get("status") or "MISSING"),
                }
            )
            rows = ranking.get("top_speakers") if isinstance(ranking.get("top_speakers"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "(未知)")
                identity = str(row.get("identity_key") or f"name:{name.casefold()}")
                item = contributors.setdefault(identity, {"identity_key": identity, "name": name, "count": 0})
                item["name"] = name
                item["count"] += _nonnegative_int(
                    row.get("count") or 0,
                    f"{run_date}.top_speakers.count",
                )
            prompt_meta = run.get("prompt_meta") if isinstance(run.get("prompt_meta"), dict) else {}
            selection = prompt_meta.get("topic_selection") if isinstance(prompt_meta.get("topic_selection"), dict) else {}
            candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("selected"):
                    title = str(candidate.get("title") or "").strip()
                    if title:
                        topics[title] += 1
            current += timedelta(days=1)
        ordered_contributors = sorted(
            contributors.values(),
            key=lambda item: (-int(item["count"]), str(item["name"]).casefold(), str(item["identity_key"])),
        )
        return {
            "message_count": sum(item["message_count"] for item in daily),
            "daily": daily,
            "contributors": ordered_contributors[:10],
            "topics": [
                {"title": title, "days": count}
                for title, count in sorted(topics.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            "missing_days": missing_days,
            "source": "saved_daily_rankings_and_summaries",
            "raw_messages_uploaded": False,
        }

    @staticmethod
    def _deterministic_narrative(group: Group, aggregate: dict, start: str, end: str) -> str:
        daily = aggregate["daily"]
        peak = max(daily, key=lambda item: (item["message_count"], item["date"]))
        contributors = "、".join(
            f"{item['name']}（{item['count']}）" for item in aggregate["contributors"][:3]
        ) or "暂无可用排行"
        topics = "、".join(item["title"] for item in aggregate["topics"][:3]) or "暂无已保存话题摘要"
        missing = f"；缺少 {len(aggregate['missing_days'])} 天日报" if aggregate["missing_days"] else ""
        return (
            f"{group.display_name}｜{start} 至 {end} 周度洞察\n"
            f"本周共 {aggregate['message_count']} 条可统计消息，活跃峰值为 {peak['date']}（{peak['message_count']} 条）{missing}。\n"
            f"主要贡献：{contributors}。\n主要话题：{topics}。"
        )

    def _render_card(self, group: Group, aggregate: dict, start: str, end: str, path: Path) -> None:
        canvas = Image.new("RGB", (1024, 1280), "#F4F7FB")
        draw = ImageDraw.Draw(canvas)
        title_font, _ = _load_font(48, self.settings.image_fallback_font_path)
        section_font, _ = _load_font(32, self.settings.image_fallback_font_path)
        body_font, _ = _load_font(26, self.settings.image_fallback_font_path)
        small_font, _ = _load_font(21, self.settings.image_fallback_font_path)
        draw.rounded_rectangle((52, 44, 972, 220), radius=28, fill="#173B57")
        draw.text((84, 74), str(group.display_name)[:22], font=title_font, fill="white")
        draw.text((84, 152), f"{start} — {end} · 每周洞察", font=body_font, fill="#D7EAF7")
        draw.rounded_rectangle((52, 252, 972, 390), radius=22, fill="white")
        draw.text((84, 280), f"消息 {aggregate['message_count']}", font=section_font, fill="#173B57")
        draw.text((520, 280), f"覆盖 {7 - len(aggregate['missing_days'])}/7 天", font=section_font, fill="#173B57")
        daily = aggregate["daily"]
        max_count = max([int(item["message_count"]) for item in daily] or [1]) or 1
        y = 450
        draw.text((64, y), "活跃趋势", font=section_font, fill="#173B57")
        y += 60
        for item in daily:
            draw.text((76, y), item["date"][5:], font=small_font, fill="#536879")
            width = max(8, int(620 * int(item["message_count"]) / max_count))
            draw.rounded_rectangle((210, y + 2, 210 + width, y + 28), radius=12, fill="#55A7D9")
            draw.text((870, y), str(item["message_count"]), font=small_font, fill="#263746")
            y += 52
        draw.text((64, 900), "主要贡献", font=section_font, fill="#173B57")
        y = 956
        for index, item in enumerate(aggregate["contributors"][:4], start=1):
            draw.text((84, y), f"{index}. {item['name']}  {item['count']}", font=body_font, fill="#263746")
            y += 46
        draw.text((550, 900), "话题变化", font=section_font, fill="#173B57")
        y = 956
        for item in aggregate["topics"][:4]:
            for line in _fit_lines(draw, f"• {item['title']}", body_font, 390, 1):
                draw.text((566, y), line, font=body_font, fill="#263746")
            y += 46
        draw.text((64, 1234), "仅聚合已保存日报，不上传整周原始聊天", font=small_font, fill="#758493")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".png.tmp")
        canvas.save(temp, format="PNG")
        temp.replace(path)

    def send_due(self, *, now: datetime | None = None) -> list[dict]:
        if not self.settings.weekly_send_enabled:
            return [{"status": "not_run", "detail": "周报发送灰度闸门未开启"}]
        tz = ZoneInfo(self.settings.app_timezone)
        now = now or datetime.now(tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=tz)
        if now.weekday() != 0:
            return [{"status": "not_run", "detail": "今天不是周一"}]
        due = time.fromisoformat(self.settings.weekly_send_time)
        due_at = datetime.combine(now.date(), due, tzinfo=tz)
        if now < due_at:
            return [{"status": "not_run", "detail": "尚未到周报发送时间"}]
        week_start, week_end = previous_natural_week(now.date())
        all_states = [
            item
            for item in self.store.list_states()
            if item.get("week_start") == week_start.isoformat()
            and item.get("week_end") == week_end.isoformat()
        ]
        results: list[dict] = []
        for state in all_states:
            if state.get("status") != "sending":
                continue
            expires_text = str(state.get("send_claim_expires_at") or "")
            try:
                expired = datetime.fromisoformat(expires_text) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                group_id = int(state.get("group_id") or 0)
                self.store.update(
                    week_start.isoformat(),
                    week_end.isoformat(),
                    group_id,
                    status="needs_attention",
                    send_claim_id="",
                    send_claim_expires_at="",
                    **_failure_fields(
                        "WEEKLY_SEND_RESULT_UNKNOWN",
                        "send_claim",
                        "发送租约过期，提交结果未知",
                    ),
                )
                results.append(
                    {
                        "group_name": str(state.get("group_name") or group_id),
                        "status": "held",
                        "error_type": "WEEKLY_SEND_RESULT_UNKNOWN",
                    }
                )
        states = [item for item in all_states if item.get("status") == "ready_to_send"]
        groups = {int(group.id): group for group in self._groups() if group.id is not None}
        for state in states:
            group_id = int(state.get("group_id") or 0)
            group = groups.get(group_id)
            if group is None or not bool(group.wechat_send_enabled):
                continue
            target = effective_send_target(group)
            if target != str(state.get("send_target_snapshot") or ""):
                self.store.update(
                    week_start.isoformat(), week_end.isoformat(), group_id,
                    status="needs_attention",
                    **_failure_fields(
                        "WEEKLY_SEND_TARGET_CHANGED",
                        "send_preflight",
                        "发送目标与生成时快照不一致",
                    ),
                )
                results.append({"group_name": group.display_name, "status": "held", "error_type": "WEEKLY_SEND_TARGET_CHANGED"})
                continue
            text_path = self.store.text_path(week_start.isoformat(), week_end.isoformat(), group_id)
            card_path = self.store.card_path(week_start.isoformat(), week_end.isoformat(), group_id)
            try:
                text_bytes = text_path.read_bytes()
                card_bytes = card_path.read_bytes()
            except OSError as exc:
                error_type = "WEEKLY_ARTIFACT_MISSING"
                self.store.update(
                    week_start.isoformat(), week_end.isoformat(), group_id,
                    status="needs_attention",
                    send_error=str(exc)[:300],
                    **_failure_fields(error_type, "send_preflight", str(exc)),
                )
                results.append({"group_name": group.display_name, "status": "held", "error_type": error_type})
                continue
            image_ok, image_detail = verify_image(card_path)
            hashes_match = (
                _sha256_bytes(text_bytes) == str(state.get("text_sha256") or "")
                and _sha256_bytes(card_bytes) == str(state.get("card_sha256") or "")
            )
            if not image_ok or not text_bytes.strip() or not hashes_match:
                error_type = "WEEKLY_ARTIFACT_HASH_MISMATCH"
                self.store.update(
                    week_start.isoformat(), week_end.isoformat(), group_id,
                    status="needs_attention",
                    send_error=(image_detail if not image_ok else "周报文字或卡片哈希不一致"),
                    **_failure_fields(
                        error_type,
                        "send_preflight",
                        image_detail if not image_ok else "周报文字或卡片哈希不一致",
                    ),
                )
                results.append({"group_name": group.display_name, "status": "held", "error_type": error_type})
                continue
            claim_id, state = self.store.claim_send(
                week_start.isoformat(),
                week_end.isoformat(),
                group_id,
                now=now,
            )
            if not claim_id:
                continue
            try:
                text_result, image_result = self.sender.send_bundle(
                    target,
                    text_bytes.decode("utf-8"),
                    card_path,
                )
            except Exception as exc:
                self.store.update(
                    week_start.isoformat(), week_end.isoformat(), group_id,
                    status="needs_attention",
                    send_error=str(exc)[:300], send_claim_id="", send_claim_expires_at="",
                    **_failure_fields("WEEKLY_SEND_RESULT_UNKNOWN", "send", str(exc)),
                )
                results.append({"group_name": group.display_name, "status": "held", "error_type": "WEEKLY_SEND_RESULT_UNKNOWN"})
                break
            text_ok = bool(
                text_result.success
                and text_result.submitted
                and not text_result.outcome_unknown
                and text_result.verification_level == "ui_observed"
            )
            image_ok = bool(
                image_result is not None
                and image_result.success
                and image_result.submitted
                and not image_result.outcome_unknown
                and image_result.verification_level == "ui_observed"
            )
            if text_ok and image_ok:
                self.store.update(
                    week_start.isoformat(), week_end.isoformat(), group_id,
                    status="sent", sent_at=now.isoformat(), send_target=target,
                    send_claim_id="", send_claim_expires_at="",
                    send_result={
                        "text": text_result.detail,
                        "image": image_result.detail,
                        "verification_level": image_result.verification_level or text_result.verification_level,
                    },
                )
                results.append({"group_name": group.display_name, "status": "sent"})
                continue
            unknown = bool(
                text_result.outcome_unknown
                or (image_result and image_result.outcome_unknown)
                or (text_result.submitted and not text_ok)
                or (image_result and image_result.submitted and not image_ok)
            )
            error_type = "WEEKLY_SEND_RESULT_UNKNOWN" if unknown else "WEEKLY_SEND_FAILED"
            self.store.update(
                week_start.isoformat(), week_end.isoformat(), group_id,
                status="needs_attention",
                send_claim_id="", send_claim_expires_at="",
                send_error=f"text={text_result.detail}; image={getattr(image_result, 'detail', '')}"[:300],
                **_failure_fields(
                    error_type,
                    "send",
                    f"text={text_result.detail}; image={getattr(image_result, 'detail', '')}",
                ),
            )
            results.append({"group_name": group.display_name, "status": "held", "error_type": error_type})
            if unknown:
                break
        return results or [{"status": "not_run", "detail": "没有待发送周报"}]
