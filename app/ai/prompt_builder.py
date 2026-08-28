"""V2 ImagePromptBuilder（Codex GPT 主用、DeepSeek 备用）实现。

输入：标准化聊天内容 + 群名 + 统计周期 + 消息数 + 发言人数 + 生图 Prompt 模板
输出：image_prompt.txt（可直接交给 Codex `$imagegen` / GPT Image 2）

策略：
- 复用 V1/V2 共用的 Codex GPT / DeepSeek 主备调用（重试/超时）；
- 主模型固定使用 settings.codex_summary_model（默认 gpt-5.6-sol）；
- 模板（templates/image_prompt/）控制最终 Prompt 的输出结构，可编辑；
- 超长聊天采用「分块 → 逐块提取事件(JSON) → 合并去重 → 按模板生成」，
  避免简单暴力截断丢失重要内容；
- 模型调用结构化元数据写入 meta（不含 API Key）。
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from time import perf_counter
from datetime import datetime

from app.ai.prompt_templates import (
    ImagePromptTemplateError,
    ImagePromptTemplateService,
    validate_image_prompt_template,
)
from app.ai.poster_copy import (
    POSTER_COPY_VERSION,
    POSTER_EDITOR_SYSTEM,
    PosterCopyError,
    build_poster_editor_prompt,
    build_poster_editor_source,
    parse_poster_copy,
    render_poster_prompt,
)
from app.ai.prompt_builder_types import PromptInput, PromptOutput
from app.ai.prompt_safety import enforce_prompt_budget, sanitize_prompt_text
from app.ai.image_themes import ImageThemeError, resolve_image_theme
from app.ai.layouts import (
    IMAGE_LAYOUT_DEFINITIONS,
    LAYOUT_DIRECTOR_SYSTEM,
    LayoutPlan,
    LayoutPlanError,
    build_layout_director_prompt,
    detect_explicit_style_layout,
    fallback_layout_plan,
    fixed_layout_plan,
    parse_layout_plan,
    preferred_layout_from_style,
    restored_layout_plan,
    selected_topic_ids,
)
from app.ai.concurrency import normalized_limit, run_ai_tasks_ordered
from app.ai.conversation_segments import (
    HARD_CHUNK_CHARS,
    OVERLAP_MESSAGES,
    SESSION_GAP_MINUTES,
    TARGET_CHUNK_CHARS,
    ConversationChunk,
    PromptMessage,
    segment_messages,
)
from app.ai.deepseek_events import (
    EVENT_ANALYZE_SYSTEM,
    build_event_prompt,
    deduplicate_event_cards,
    parse_event_cards,
)
from app.ai.topic_selection import (
    TOPIC_CANDIDATE_SYSTEM,
    TopicSelectionError,
    build_direct_candidate_prompt,
    build_merged_candidate_prompt,
    parse_topic_candidates,
    score_and_select_topics,
    selected_topics_json,
)
from app.config.settings import Settings, get_settings
from app.providers.ai.base import ExternalCallResultUnknownError
from app.providers.ai.codex import build_summary_provider

logger = logging.getLogger("groupbrief.ai")

STRUCTURED_ANALYSIS_MAX_ATTEMPTS = 2
TOPIC_CANDIDATE_MAX_TOKENS = 8_000
EVENT_CARD_MAX_TOKENS = 6_000
LAYOUT_DIRECTOR_MAX_TOKENS = 1_800
FINAL_PROMPT_MAX_ATTEMPTS = 3
_STRUCTURED_RETRY_INSTRUCTION = """\

上一次响应不完整、被截断或不符合约定 JSON。请重新阅读原材料，并重新输出一个完整、紧凑、可解析的 JSON 对象。
不要复述要求，不要输出 Markdown；缩短标题、摘要和理由，但保留真实 message_ids 与所有必需字段。"""
_LAYOUT_RETRY_INSTRUCTION = """\

上一次分镜响应不完整或不符合 JSON 约定。请重新输出完整紧凑的 JSON 对象；
必须使用合法 layout_id 和 structure_mode；featured_topic_ids 数量必须匹配结构模式，
topic_order 与 panel_beats 必须恰好覆盖全部入选主题；至少一个话题使用两个镜头，镜头总数必须合法。"""

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """剥离模板中的 HTML 注释（供作者写说明，不进入最终 Prompt）。"""
    return _HTML_COMMENT_RE.sub("", text).strip()


def _validated_persisted_selection(selection: object, messages: list[PromptMessage]) -> dict:
    """验证已落盘选题仍完整且能回查快照；不重新调用模型选题。"""
    if not isinstance(selection, dict) or not isinstance(selection.get("candidates"), list):
        raise ValueError("已保存的选题总结缺少 candidates，已停止重建")
    result = deepcopy(selection)
    selected = [item for item in result["candidates"] if isinstance(item, dict) and item.get("selected")]
    selected_ids = [str(item.get("topic_id") or "").strip() for item in selected]
    stored_ids = result.get("selected_topic_ids")
    if not isinstance(stored_ids, list) or selected_ids != [str(value) for value in stored_ids]:
        raise ValueError("已保存的选题 ID 与入选标记不一致，已停止重建")
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("已保存的选题 ID 为空或重复，已停止重建")
    allowed_message_ids = {message.message_id for message in messages}
    for item in selected:
        if not str(item.get("title") or "").strip() or not str(item.get("summary") or "").strip():
            raise ValueError("已保存的入选主题缺少标题或总结，已停止重建")
        evidence_ids = item.get("message_ids") if isinstance(item.get("message_ids"), list) else []
        if not evidence_ids or any(str(message_id) not in allowed_message_ids for message_id in evidence_ids):
            raise ValueError("已保存的入选主题无法从 messages.json 回查，已停止重建")
        quotes = item.get("quotes") if isinstance(item.get("quotes"), list) else []
        if not any(str(value).strip() for value in quotes):
            raise ValueError("已保存的入选主题缺少真实原话，已停止重建")
        visible_people = item.get("visible_participants") if isinstance(item.get("visible_participants"), list) else []
        if not any(str(value).strip() for value in visible_people) and not str(item.get("participant_label") or "").strip():
            raise ValueError("已保存的入选主题缺少可见人物，已停止重建")
    selected_topics_json(result)
    return result


_MEDIA_PREFIX = {
    "image": "[图片]",
    "emoji": "[表情]",
    "voice": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "red_packet": "[红包]",
    "chat_history": "[聊天记录]",
    "transfer": "[转账]",
}


def _to_ai_text(m) -> str:
    content = m.content or ""
    prefix = _MEDIA_PREFIX.get(m.message_type, "")
    if m.message_type in {"image", "file"}:
        return prefix
    if not content and prefix:
        return prefix
    if prefix and not content.startswith("["):
        return f"{prefix} {content}"
    return content


class DeepSeekImagePromptBuilder:
    """保留历史类名以兼容现有注入点；默认实现已切换为 Codex GPT。"""

    name = "codex-gpt-image-prompt"

    def __init__(
        self,
        settings: Settings | None = None,
        templates: ImagePromptTemplateService | None = None,
        provider=None,
        *,
        summary_settings: Settings | None = None,
        summary_provider=None,
        prompt_provider=None,
    ):
        self.settings = settings or get_settings()
        self.summary_settings = summary_settings or self.settings
        self.templates = templates or ImagePromptTemplateService()
        # 兼容旧注入点：显式 provider 仍同时承担分析与海报 Prompt。
        # 生产路径允许两项能力分别选择白名单 Provider/模型。
        if provider is not None:
            self._summary_provider = provider
            self._prompt_provider = provider
        else:
            self._summary_provider = summary_provider or build_summary_provider(
                self.summary_settings
            )
            self._prompt_provider = prompt_provider or build_summary_provider(
                self.settings
            )
        self._provider = self._summary_provider

    # ---------- 对外 ----------

    def build(self, data: PromptInput) -> PromptOutput:
        started_at = perf_counter()
        api_model = self._prompt_provider.model
        seen_providers: set[int] = set()
        for provider_instance in (self._summary_provider, self._prompt_provider):
            if id(provider_instance) in seen_providers:
                continue
            seen_providers.add(id(provider_instance))
            reset_usage = getattr(provider_instance, "reset_usage", None)
            if callable(reset_usage):
                reset_usage()
        meta: dict | None = None
        try:
            theme = resolve_image_theme(
                data.image_theme,
                data.image_theme_custom,
                group_key=data.group_id or data.group_name,
                run_date=data.run_date,
                previous_signature=data.previous_theme_signature,
                persisted_meta=data.persisted_theme_meta,
            )
            template_source = data.template_override or self.templates.read(data.template)
            template_text = _strip_html_comments(template_source)
            validate_image_prompt_template(template_text)
            report_date = (data.report_date or data.period_end[:10]).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
                raise ValueError("report_date 必须来自统计周期并使用 YYYY-MM-DD")
            theme_text = theme.visible_text
            explicit_theme_text = theme_text if theme.has_explicit_style else ""
            visible_group_name, _ = sanitize_prompt_text(
                data.visible_group_name or data.group_name,
                allow_newlines=False,
            )
            if not visible_group_name:
                raise ValueError("群聊显示名不能为空")
            period_line = f"{data.period_start} ~ {data.period_end}"
            message_line = f"{data.message_count} 条消息"
            speaker_line = f"{data.speaker_count} 人发言"

            messages = [self._to_prompt_message(message, index) for index, message in enumerate(data.messages, start=1)]
            messages = [message for message in messages if message.text]
            direct_chars = max(1_000, int(self.settings.max_context_chars or 50_000))
            chunks: list[ConversationChunk] = []
            if data.persisted_topic_selection is None:
                chunks = segment_messages(
                    messages,
                    direct_chars=direct_chars,
                    target_chars=min(TARGET_CHUNK_CHARS, direct_chars),
                    hard_chars=max(HARD_CHUNK_CHARS, direct_chars),
                    session_gap_minutes=SESSION_GAP_MINUTES,
                    overlap_messages=OVERLAP_MESSAGES,
                )
                if not chunks:
                    raise ValueError("没有可提交给总结模型的聊天文本")
            elif not messages:
                raise ValueError("messages.json 为空，无法回查已保存选题")
            meta = {
                "template": data.template,
                "template_source": "group_override" if data.template_override else "global",
                "api_model": api_model,
                "primary_provider": self.settings.summary_provider_primary,
                "fallback_provider": self.settings.summary_provider_fallback,
                "summary_primary_provider": self.summary_settings.summary_provider_primary,
                "summary_fallback_provider": self.summary_settings.summary_provider_fallback,
                "prompt_primary_provider": self.settings.summary_provider_primary,
                "prompt_fallback_provider": self.settings.summary_provider_fallback,
                "message_lines": len(messages),
                "context_chars": sum(len(message.text) for message in messages),
                "chunk_count": len(chunks),
                "generated_at": datetime.now().isoformat(),
                "report_date": report_date,
            }
            meta.update(theme.to_meta())
            meta["style_intervention"] = theme.has_explicit_style

            if data.persisted_topic_selection is not None:
                selection = _validated_persisted_selection(data.persisted_topic_selection, messages)
                meta["mode"] = "persisted_topic_selection"
                meta["topic_selection_reused"] = True
                meta["reuse_source"] = "run.prompt_meta"
                analysis_calls = 0
            elif len(chunks) <= 1:
                meta["mode"] = "direct"
                candidates, candidate_calls = self._topic_candidates_with_retry(
                    TOPIC_CANDIDATE_SYSTEM,
                    build_direct_candidate_prompt(chunks[0]),
                    chunks[0].message_ids,
                )
                analysis_calls = candidate_calls
            else:
                meta["mode"] = "natural_chunked"
                indexed_chunks = list(enumerate(chunks, start=1))

                def analyze(item: tuple[int, ConversationChunk]) -> tuple[list[dict], int]:
                    idx, chunk = item
                    event_system = EVENT_ANALYZE_SYSTEM
                    if explicit_theme_text:
                        event_system += "\n\n" + self._theme_constraint(explicit_theme_text)
                    return self._event_cards_with_retry(
                        event_system,
                        build_event_prompt(chunk, f"第 {idx}/{len(chunks)} 块"),
                        chunk,
                    )

                analysis_results = run_ai_tasks_ordered(
                    analyze,
                    indexed_chunks,
                    max_workers=normalized_limit(self.settings.ai_request_concurrency, 6),
                )
                analyses = [cards for cards, _ in analysis_results]
                event_calls = sum(calls for _, calls in analysis_results)
                cards = deduplicate_event_cards(analyses)
                if not cards:
                    raise ValueError("总结模型未从超长聊天中提取到可验证事件")
                meta["event_count"] = len(cards)
                candidates, candidate_calls = self._topic_candidates_with_retry(
                    TOPIC_CANDIDATE_SYSTEM,
                    build_merged_candidate_prompt(cards),
                    (message.message_id for message in messages),
                )
                analysis_calls = event_calls + candidate_calls

            if data.persisted_topic_selection is None:
                selection = score_and_select_topics(candidates, messages)
                meta["topic_selection_reused"] = False
            meta["topic_selection_version"] = selection["topic_selection_version"]
            meta["topic_selection"] = selection
            selected_payload = selected_topics_json(selection)
            topic_ids = selected_topic_ids(selection)
            recent_history = tuple(data.recent_layout_history or ())[:3]
            custom_style_text = theme.custom_text if theme.requested_key == "custom" else ""
            style_layout_locked = detect_explicit_style_layout(custom_style_text)
            preferred_layout = preferred_layout_from_style(custom_style_text)

            if data.persisted_topic_selection is not None:
                layout = restored_layout_plan(
                    data.persisted_theme_meta,
                    topic_ids,
                    style_layout_locked=style_layout_locked,
                )
                if layout is None:
                    raise ValueError("已保存的漫画分镜无法覆盖全部入选主题，已停止重建")
                layout_calls = 0
            elif preferred_layout:
                layout = fixed_layout_plan(
                    preferred_layout,
                    topic_ids,
                    recent_history=recent_history,
                )
                layout_calls = 0
            else:
                layout = restored_layout_plan(
                    data.persisted_theme_meta,
                    topic_ids,
                    style_layout_locked=style_layout_locked,
                )
                if layout is not None:
                    layout_calls = 0
                else:
                    layout, layout_calls = self._layout_plan_with_retry(
                        selected_payload,
                        topic_ids,
                        theme_text=explicit_theme_text,
                        recent_history=recent_history,
                        style_layout_locked=style_layout_locked,
                        seed_text=f"{data.group_id or data.group_name}|{data.run_date}",
                    )

            meta.update(layout.to_meta())
            meta["recent_layout_ids"] = [
                str(item.get("layout_id") or "")
                for item in recent_history
                if isinstance(item, dict) and item.get("layout_id")
            ]

            editor_source = build_poster_editor_source(selection, layout)
            final_user_prompt = build_poster_editor_prompt(editor_source)
            text = ""
            final_calls = 0
            last_violations: list[str] = []
            for attempt in range(FINAL_PROMPT_MAX_ATTEMPTS):
                prompt = final_user_prompt
                if attempt:
                    prompt += (
                        "\n\n上一次漫画编辑 JSON 未通过证据或结构校验。"
                        "请重新输出完整 JSON，不要解释，不要复用错误内容。"
                    )
                    if last_violations:
                        prompt += "\n上次具体违反：" + "；".join(last_violations[:8])
                raw_copy = self._prompt_chat(
                    POSTER_EDITOR_SYSTEM,
                    prompt,
                    response_format="json_object",
                    temperature=0.35,
                    max_tokens=6000,
                )
                final_calls += 1
                try:
                    copy = parse_poster_copy(raw_copy, editor_source)
                    candidate_text = render_poster_prompt(
                        copy,
                        group_name=visible_group_name,
                        period_line=period_line,
                        message_line=message_line,
                        speaker_line=speaker_line,
                        style_text=theme_text,
                        explicit_style=theme.has_explicit_style,
                        template_text=template_text,
                    )
                except PosterCopyError as exc:
                    last_violations = [str(exc)]
                    logger.warning(
                        "漫画编辑稿校验失败（第 %s/%s 次）：%s",
                        attempt + 1,
                        FINAL_PROMPT_MAX_ATTEMPTS,
                        last_violations,
                    )
                    continue
                text = candidate_text
                meta["poster_copy_version"] = POSTER_COPY_VERSION
                meta["poster_topic_count"] = len(copy.panels)
                meta["poster_visible_participant_count"] = sum(
                    len(panel.participants) for panel in copy.panels
                )
                break
            if not text:
                raise ValueError("最终生图 Prompt 未通过固定漫画合同：" + "；".join(last_violations[:8]))

            meta["api_call_count"] = analysis_calls + layout_calls + final_calls
            summary_actual = (
                self._provider_actual(
                    self._summary_provider,
                    self.summary_settings,
                )
                if analysis_calls
                else {
                    "provider": "",
                    "model": "",
                    "providers_used": [],
                    "fallback_reason": "",
                }
            )
            prompt_actual = self._provider_actual(
                self._prompt_provider,
                self.settings,
            )
            meta["summary_provider_actual"] = summary_actual["provider"]
            meta["summary_model_actual"] = summary_actual["model"]
            meta["summary_fallback_reason"] = summary_actual["fallback_reason"]
            meta["summary_providers_used"] = summary_actual["providers_used"]
            meta["summary_api_call_count"] = analysis_calls
            meta["prompt_provider_actual"] = prompt_actual["provider"]
            meta["prompt_model_actual"] = prompt_actual["model"]
            meta["prompt_fallback_reason"] = prompt_actual["fallback_reason"]
            meta["prompt_providers_used"] = prompt_actual["providers_used"]
            meta["prompt_api_call_count"] = layout_calls + final_calls
            # 旧字段继续表示最终海报 Prompt 能力，供旧 run.json 读取器兼容。
            meta["actual_provider"] = prompt_actual["provider"]
            meta["actual_model"] = prompt_actual["model"]
            meta["providers_used"] = sorted(
                set(summary_actual["providers_used"] + prompt_actual["providers_used"])
            )
            meta["fallback_reason"] = prompt_actual["fallback_reason"]

            summary_ms = round((perf_counter() - started_at) * 1000)
            meta["summary_ms"] = summary_ms
            # 保留旧字段一版，避免历史运行分析与外部读取立即失效。
            meta["deepseek_ms"] = summary_ms
            text, prompt_budget_meta = enforce_prompt_budget(
                text,
                max_chars=self.settings.image_prompt_max_chars,
                max_bytes=self.settings.image_prompt_max_bytes,
            )
            meta.update(prompt_budget_meta)
            return PromptOutput(success=True, prompt=text, model=api_model, meta=meta)
        except (ImagePromptTemplateError, ImageThemeError, LayoutPlanError, ValueError) as e:
            logger.warning("Prompt 模板错误：%s", e)
            return PromptOutput(
                success=False,
                error=str(e)[:300],
                model=api_model,
                meta=meta,
            )
        except ExternalCallResultUnknownError:
            # Pipeline 需要把提交后断线/超时持久化为 result_unknown，不能降格成普通失败。
            raise
        except Exception as e:  # 主备模型调用失败等
            logger.exception("ImagePromptBuilder 生成失败")
            return PromptOutput(
                success=False,
                error=str(e)[:300],
                model=api_model,
                meta=meta,
            )

    # ---------- 内部 ----------

    def _to_prompt_message(self, message, index: int) -> PromptMessage:
        timestamp = message.timestamp if hasattr(message.timestamp, "strftime") else None
        message_id = (
            getattr(message, "message_id", "")
            or getattr(message, "content_hash", "")
            or f"v2-{index}"
        )
        safe_sender, _ = sanitize_prompt_text(
            message.sender_name or "(未知)",
            allow_newlines=False,
        )
        safe_text, _ = sanitize_prompt_text(_to_ai_text(message))
        return PromptMessage(
            message_id=str(message_id),
            timestamp=timestamp,
            sender_name=safe_sender or "(未知)",
            text=safe_text,
            sender_id=str(getattr(message, "sender_id", "") or ""),
        )

    def _to_line(self, message) -> str:
        """兼容旧测试/诊断调用。"""
        item = self._to_prompt_message(message, 1)
        ts = item.timestamp.strftime("%H:%M") if item.timestamp else ""
        return f"[{ts}] {item.sender_name}: {item.text}"

    def _chunk(self, lines: list[str]) -> list[str]:
        """兼容旧调用；不再按消息条数切分。"""
        if not lines:
            return []
        messages = [PromptMessage(f"legacy-{index}", None, "", line) for index, line in enumerate(lines, start=1)]
        chunks = segment_messages(messages, direct_chars=max(1_000, int(self.settings.max_context_chars or 50_000)))
        return [chunk.text for chunk in chunks]

    @staticmethod
    def _theme_constraint(theme_prompt: str) -> str:
        return (
            "【大主题约束｜全图最高视觉约束】\n"
            + theme_prompt
            + "\n大主题控制全图配色、画材、服装、造型、装饰、纹理、光影和画风；"
            "不得创造、补充或改写聊天事实，也不得被整体版式替换或削弱。"
            "重新生图只允许改变所选美术家族及当天已解析的视觉细节，日期、数字、人物、逐字气泡、话题覆盖和既定分镜都是不变量。"
        )

    def _analysis_chat(
        self,
        system: str,
        user_prompt: str,
        *,
        response_format: str = "json_object",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> str:
        """结构化分析调用不混入最终海报格式约束，避免候选阶段角色冲突。"""
        return self._summary_provider._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _prompt_chat(
        self,
        system: str,
        user_prompt: str,
        *,
        response_format: str = "json_object",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> str:
        """最终版式与海报编辑调用使用群级 Prompt Provider。"""
        return self._prompt_provider._chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _provider_actual(provider, settings: Settings) -> dict:
        actual_provider = str(
            getattr(provider, "last_provider_used", "")
            or getattr(provider, "name", "unknown")
        )
        actual_model = str(
            settings.ai_model
            if actual_provider == "deepseek"
            else getattr(provider, "model", settings.codex_summary_model)
        )
        providers_used = list(getattr(provider, "providers_used", []) or [])
        return {
            "provider": actual_provider,
            "model": actual_model,
            "providers_used": providers_used or [actual_provider],
            "fallback_reason": str(
                getattr(provider, "last_fallback_reason", "") or ""
            ),
        }

    def _layout_plan_with_retry(
        self,
        selected_topics_payload: str,
        topic_ids: list[str],
        *,
        theme_text: str,
        recent_history: tuple[dict, ...],
        style_layout_locked: bool,
        seed_text: str,
    ) -> tuple[LayoutPlan, int]:
        """版式 JSON 无效时完整重做一次，再使用不编造事实的确定性回退。"""
        history = tuple(item for item in recent_history if isinstance(item, dict))[:3]
        previous_layout_id = next(
            (
                str(item.get("layout_id") or "")
                for item in history
                if str(item.get("layout_id") or "") in IMAGE_LAYOUT_DEFINITIONS
            ),
            "",
        )
        user_prompt = build_layout_director_prompt(
            selected_topics_payload,
            theme_text=theme_text,
            recent_history=history,
            style_layout_locked=style_layout_locked,
        )
        last_error: LayoutPlanError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _LAYOUT_RETRY_INSTRUCTION
            raw = self._prompt_chat(
                LAYOUT_DIRECTOR_SYSTEM,
                prompt,
                response_format="json_object",
                temperature=0.4,
                max_tokens=LAYOUT_DIRECTOR_MAX_TOKENS,
            )
            try:
                return (
                    parse_layout_plan(
                        raw,
                        topic_ids,
                        previous_layout_id=previous_layout_id,
                        style_layout_locked=style_layout_locked,
                    ),
                    attempt + 1,
                )
            except LayoutPlanError as exc:
                last_error = exc
                logger.warning(
                    "版式导演结构化响应校验失败（第 %s/%s 次）：%s",
                    attempt + 1,
                    STRUCTURED_ANALYSIS_MAX_ATTEMPTS,
                    exc,
                )
        assert last_error is not None
        logger.warning("版式导演连续失败，使用确定性安全回退：%s", last_error)
        return (
            fallback_layout_plan(
                topic_ids,
                recent_history=history,
                seed_text=seed_text,
                style_layout_locked=style_layout_locked,
            ),
            STRUCTURED_ANALYSIS_MAX_ATTEMPTS,
        )

    def _topic_candidates_with_retry(
        self,
        system: str,
        user_prompt: str,
        allowed_message_ids,
    ) -> tuple[list[dict], int]:
        """格式或证据校验失败时完整重做一次，绝不接受截断 JSON。"""
        allowed_ids = tuple(allowed_message_ids)
        last_error: TopicSelectionError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _STRUCTURED_RETRY_INSTRUCTION
            raw = self._analysis_chat(
                system,
                prompt,
                response_format="json_object",
                temperature=0.1,
                max_tokens=TOPIC_CANDIDATE_MAX_TOKENS,
            )
            try:
                return parse_topic_candidates(raw, allowed_ids), attempt + 1
            except TopicSelectionError as exc:
                last_error = exc
                logger.warning("候选主题结构化响应校验失败（第 %s/%s 次）：%s", attempt + 1, STRUCTURED_ANALYSIS_MAX_ATTEMPTS, exc)
        assert last_error is not None
        raise last_error

    def _event_cards_with_retry(
        self,
        system: str,
        user_prompt: str,
        chunk: ConversationChunk,
    ) -> tuple[list[dict], int]:
        """片段事件 JSON 失败时完整重做一次，并返回真实模型调用次数。"""
        last_error: ValueError | None = None
        for attempt in range(STRUCTURED_ANALYSIS_MAX_ATTEMPTS):
            prompt = user_prompt if attempt == 0 else user_prompt + _STRUCTURED_RETRY_INSTRUCTION
            raw = self._analysis_chat(
                system,
                prompt,
                response_format="json_object",
                temperature=0.1,
                max_tokens=EVENT_CARD_MAX_TOKENS,
            )
            try:
                return parse_event_cards(raw, chunk), attempt + 1
            except ValueError as exc:
                last_error = exc
                logger.warning("片段事件结构化响应校验失败（第 %s/%s 次）：%s", attempt + 1, STRUCTURED_ANALYSIS_MAX_ATTEMPTS, exc)
        assert last_error is not None
        raise last_error


# 新代码使用中性名称；历史导入继续可用，避免破坏已有测试和扩展注入点。
GroupSummaryImagePromptBuilder = DeepSeekImagePromptBuilder
