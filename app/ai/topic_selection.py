"""有证据、喜剧优先的日报候选主题评分与 1～7 个高密度动态选题。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
import unicodedata
from typing import Any, Iterable

from app.services.speaker_identity import build_speaker_stats, speaker_name_sort_key

from app.ai.conversation_segments import ConversationChunk, PromptMessage

TOPIC_SELECTION_VERSION = "7.0"
MAX_CANDIDATES = 10
MIN_SELECTED = 1
TARGET_SELECTED = 5
MAX_SELECTED = 7
HIGH_VOLUME_MESSAGE_THRESHOLD = 200
SELECTION_MIN_SCORE = 60.0
SELECTION_MAX_GAP = 15.0
VISIBLE_PARTICIPANT_LIMIT = 4
VISIBLE_PARTICIPANT_CHAR_BUDGET = 48
UNRESOLVED_PARTICIPANT_LABEL = "群友（昵称未识别）"

POLITICAL_KEYWORD_POLICY_VERSION = "political-keywords-v1"
# 仅包含明确政治名词，不维护人物姓名，也不使用容易误伤日常聊天的宽泛词。
POLITICAL_TOPIC_KEYWORDS: tuple[str, ...] = (
    "地缘政治",
    "外交制裁",
    "主权争议",
    "人大常委会",
    "全国人大",
    "人大代表",
    "全国政协",
    "政协委员",
    "国家主席",
    "副总统",
    "执政党",
    "在野党",
    "党代会",
    "总书记",
    "参议院",
    "众议院",
    "政治",
    "政党",
    "两会",
    "选举",
    "大选",
    "竞选",
    "公投",
    "总统",
    "总理",
    "首相",
    "国会",
    "议会",
    "内阁",
    "弹劾",
    "政变",
    "示威",
    "抗议",
    "台独",
    "港独",
    "藏独",
    "疆独",
    "standing committee of the national people's congress",
    "chinese people's political consultative conference",
    "national people's congress",
    "house of representatives",
    "hong kong independence",
    "taiwan independence",
    "tibetan independence",
    "xinjiang independence",
    "presidential election",
    "diplomatic sanctions",
    "sovereignty dispute",
    "opposition party",
    "political party",
    "political campaign",
    "party congress",
    "general election",
    "general secretary",
    "vice president",
    "prime minister",
    "ruling party",
    "cppcc member",
    "npc deputy",
    "head of state",
    "military coup",
    "political protest",
    "political demonstration",
    "geopolitics",
    "geopolitical",
    "referendum",
    "impeachment",
    "parliament",
    "president",
    "premier",
    "election",
    "campaign",
    "congress",
    "senate",
    "cabinet",
    "protest",
    "demonstration",
    "politics",
    "political",
    "cppcc",
    "coup",
)

SCORE_WEIGHTS = {
    "comedy": 40.0,
    "group_recognition": 20.0,
    "visual": 20.0,
    "discussion": 10.0,
    "participation": 5.0,
    "continuity": 5.0,
}

TOPIC_CANDIDATE_SYSTEM = """你是群聊日报选题编辑。只能基于给定消息或事件卡整理候选主题。
必须返回一个 JSON 对象，不得输出 Markdown 或解释。候选主题必须引用真实 message_ids，禁止虚构。
你只负责选择 message_ids 和概括事件，不得输出 people、quotes 或任何人物姓名；
人物身份、显示名和逐字原话全部由程序按 message_id 从当前快照回填。
事实真实性是准入门槛；通过真实性校验后，好玩程度是第一排序目标。
内容充足时输出 10 个候选，证据不足时允许少于 10 个；共享同一核心事实、里程碑或结论的相似话题必须合并，
不得为了凑数把一个事件拆成“发起/回应”或重复角度。
comedy_score 为 0～40，group_recognition_score 为 0～20，visual_score 为 0～20。
comedy_angle 说明真实笑点，visual_gag 说明不改变事实的视觉笑点，并给出简短 score_reason。
为避免响应截断，标题和评分理由必须简洁；summary 必须是不含人物姓名的一句完整事件概括，建议不超过 60 个汉字，
不得以省略号、半句话或残缺表情代码结尾；
每个候选的 message_ids 最多保留 100 条有效证据。"""

_CANDIDATE_SCHEMA = """返回结构：
{
  "candidates": [
    {
      "topic_id": "topic-01",
      "title": "主题短标题",
      "summary": "不含人物姓名、基于证据的一句事实过程或结论",
      "start_time": "YYYY-MM-DD HH:MM",
      "end_time": "YYYY-MM-DD HH:MM",
      "message_ids": ["真实消息ID"],
      "comedy_score": 0,
      "group_recognition_score": 0,
      "visual_score": 0,
      "comedy_angle": "真实对话中的笑点、反差或回环",
      "visual_gag": "不改变事实的视觉比喻",
      "score_reason": "评分理由"
    }
  ]
}"""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class TopicSelectionError(ValueError):
    """选题阶段可审计失败。"""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}：{detail}")


@dataclass(frozen=True)
class TopicEvidence:
    message_id: str
    sender_id: str
    sender_key: str
    sender_name: str
    text: str
    timestamp: datetime | None
    source_index: int


def build_direct_candidate_prompt(chunk: ConversationChunk) -> str:
    return (
        f"以下是完整群聊记录（{chunk.start_time} 至 {chunk.end_time}）。\n\n"
        + _CANDIDATE_SCHEMA
        + "\n\n聊天记录：\n"
        + chunk.text
    )


def build_merged_candidate_prompt(event_cards: Iterable[dict[str, Any]]) -> str:
    return (
        "以下是从多个自然会话片段中提取并校验过 message_ids 的事件卡。"
        "请语义合并后形成当天最多 10 个候选主题，不得添加卡片之外的事实。\n\n"
        + _CANDIDATE_SCHEMA
        + "\n\n事件卡：\n"
        + json.dumps({"events": list(event_cards)}, ensure_ascii=False, separators=(",", ":"))
    )


def _text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _text_list(value: Any, maximum_items: int, maximum_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:maximum_items]:
        text = _text(item, maximum_chars)
        if text and text not in result:
            result.append(text)
    return result


def _score(value: Any, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(min(max(number, 0.0), maximum), 1)


def parse_topic_candidates(raw: str, allowed_message_ids: Iterable[str]) -> list[dict[str, Any]]:
    """解析 AI 候选并严格验证证据 ID；格式或证据错误不得静默降级。"""
    cleaned = _FENCE_RE.sub("", (raw or "").strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise TopicSelectionError("TOPIC_CANDIDATES_INVALID", f"候选主题不是有效 JSON：{exc.msg}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise TopicSelectionError("TOPIC_CANDIDATES_INVALID", "响应缺少 candidates 数组")

    allowed = set(allowed_message_ids)
    candidates: list[dict[str, Any]] = []
    used_topic_ids: set[str] = set()
    for index, item in enumerate(payload["candidates"][:MAX_CANDIDATES], start=1):
        if not isinstance(item, dict):
            raise TopicSelectionError("TOPIC_CANDIDATES_INVALID", "候选主题项必须是对象")
        title = _text(item.get("title"), 120)
        summary = _text(item.get("summary", item.get("content")), 1200)
        if not title or not summary:
            raise TopicSelectionError("TOPIC_CANDIDATES_INVALID", "候选主题缺少 title/summary")
        message_ids: list[str] = []
        for message_id in _text_list(item.get("message_ids"), 100, 160):
            normalized = message_id.split("#片段", 1)[0]
            if normalized in allowed and normalized not in message_ids:
                message_ids.append(normalized)
        if not message_ids:
            raise TopicSelectionError("TOPIC_CANDIDATES_INVALID", f"候选主题“{title}”没有有效消息证据")

        topic_id = _text(item.get("topic_id"), 80) or f"topic-{index:02d}"
        if topic_id in used_topic_ids:
            topic_id = f"topic-{index:02d}"
        used_topic_ids.add(topic_id)
        candidates.append(
            {
                "topic_id": topic_id,
                "title": title,
                "summary": summary,
                "people": _text_list(item.get("people"), 30, 80),
                "quotes": _text_list(item.get("quotes"), 3, 300),
                "start_time": _text(item.get("start_time"), 40),
                "end_time": _text(item.get("end_time"), 40),
                "message_ids": message_ids,
                # 接受一版旧字段，避免旧 Provider/Fake 调用立即失效；新提示统一使用 comedy_score。
                "comedy_score": _score(item.get("comedy_score", item.get("interestingness_score")), 40.0),
                "group_recognition_score": _score(item.get("group_recognition_score"), 20.0),
                "visual_score": _score(item.get("visual_score"), 20.0),
                "comedy_angle": _text(item.get("comedy_angle"), 300),
                "visual_gag": _text(item.get("visual_gag"), 300),
                "score_reason": _text(item.get("score_reason"), 300),
            }
        )
    return candidates


def _evidence(messages: Iterable[PromptMessage]) -> dict[str, TopicEvidence]:
    result: dict[str, TopicEvidence] = {}
    for source_index, item in enumerate(messages):
        if not item.message_id:
            continue
        if item.message_id in result:
            raise TopicSelectionError(
                "TOPIC_EVIDENCE_DUPLICATE_ID",
                f"messages.json 包含重复 message_id：{item.message_id}",
            )
        result[item.message_id] = TopicEvidence(
            message_id=item.message_id,
            sender_id=item.sender_id,
            sender_key=(item.sender_id or "").casefold() or item.sender_name or "(未知)",
            sender_name=item.sender_name or "(未知)",
            text=item.text or "",
            timestamp=item.timestamp,
            source_index=source_index,
        )
    return result


_QUOTE_PUNCTUATION_RE = re.compile(r"[\s\u3000，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]《》<>…—\-_]+")


def _normalized_quote(value: str) -> str:
    return _QUOTE_PUNCTUATION_RE.sub("", unicodedata.normalize("NFKC", value or "")).casefold()


def _complete_excerpt(value: str, maximum: int) -> str:
    """保留完整可读句或连续短句，不制造悬空省略号。"""
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= maximum:
        return text
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", text) if part.strip()]
    selected = ""
    for sentence in sentences:
        if len(sentence) > maximum:
            continue
        combined = selected + sentence
        if len(combined) > maximum:
            break
        selected = combined
    if selected:
        return selected
    clauses = [part.strip() for part in re.split(r"[，,、：:]", text) if part.strip()]
    return next((part for part in clauses if len(part) <= maximum), "")


def _verified_quotes(candidate_quotes: Iterable[str], items: Iterable[TopicEvidence]) -> list[str]:
    """只保留可从所引消息回查的原话；无有效候选时使用真实原文短句。"""
    evidence_items = list(items)
    normalized_messages = [(_normalized_quote(item.text), item.text.strip()) for item in evidence_items]
    verified: list[str] = []
    for quote in candidate_quotes:
        normalized = _normalized_quote(quote)
        if not normalized:
            continue
        if any(normalized in message for message, _ in normalized_messages) and quote not in verified:
            verified.append(quote.strip())
        if len(verified) >= 3:
            break
    if verified:
        return verified

    for _, original in normalized_messages:
        excerpt = _complete_excerpt(original, 80)
        if excerpt:
            return [excerpt]
    return []


def _evidence_dialogue(items: Iterable[TopicEvidence]) -> list[dict[str, str]]:
    """给最终 Prompt 保留小而可核对的原始对话，而不是只有模型二手摘要。"""
    dialogue: list[dict[str, str]] = []
    for item in sorted(items, key=lambda evidence_item: evidence_item.source_index)[:8]:
        text = item.text.strip()
        if not text:
            continue
        excerpt = _complete_excerpt(text, 240)
        if not excerpt:
            continue
        dialogue.append(
            {
                "message_id": item.message_id,
                "sender_id": item.sender_id,
                "speaker": item.sender_name,
                "text": excerpt,
                "original_text": text,
            }
        )
    return dialogue


def _resolved_participant_name(value: str) -> bool:
    name = (value or "").strip()
    return bool(
        name
        and name not in {"(未知)", "未知"}
        and not name.startswith("未命名成员-")
    )


def _participant_attribution(items: Iterable[TopicEvidence]) -> dict[str, Any]:
    """用同一身份集合生成人数、完整名单和有限画面署名。"""
    ordered_items = sorted(items, key=lambda item: item.source_index)
    stats = build_speaker_stats(
        (item.sender_key, item.sender_name) for item in ordered_items
    )
    ranked = sorted(
        (item for item in stats if _resolved_participant_name(item.name)),
        key=lambda item: (-item.count, item.first_index, speaker_name_sort_key(item.name), item.key),
    )
    selected: list[str] = []
    used_chars = 0
    for item in ranked:
        name = item.name
        added_chars = len(name) + (1 if selected else 0)
        if selected and used_chars + added_chars > VISIBLE_PARTICIPANT_CHAR_BUDGET:
            # A long full name may not fit the remaining budget while a later,
            # shorter evidence-backed name still does. Keep scanning instead of
            # prematurely reducing the visible participant diversity.
            continue
        selected.append(name)
        used_chars += added_chars
        if len(selected) >= VISIBLE_PARTICIPANT_LIMIT:
            break

    total_participants = len(stats)
    participants = sorted((item.name for item in stats), key=speaker_name_sort_key)
    if not selected:
        return {
            "participant_count": total_participants,
            "participants": participants,
            "visible_participants": [],
            "participant_label": UNRESOLVED_PARTICIPANT_LABEL,
            "participant_name_unresolved": True,
        }

    label = "、".join(selected)
    if total_participants > len(selected):
        label += f"等 {total_participants} 人"
    return {
        "participant_count": total_participants,
        "participants": participants,
        "visible_participants": selected,
        "participant_label": label,
        "participant_name_unresolved": False,
    }


def _log_normalized(value: float, maximum: float, weight: float) -> float:
    if value <= 0 or maximum <= 0:
        return 0.0
    return round(weight * math.log1p(value) / math.log1p(maximum), 1)


def _political_keyword_matches(
    candidate: dict[str, Any], evidence_dialogue: Iterable[dict[str, Any]]
) -> list[str]:
    evidence_text = " ".join(
        str(entry.get("original_text") or entry.get("text") or "")
        for entry in evidence_dialogue
        if isinstance(entry, dict)
    )
    combined = " ".join(
        (
            str(candidate.get("title") or ""),
            str(candidate.get("summary") or ""),
            str(candidate.get("visual_gag") or ""),
            evidence_text,
        )
    )
    normalized = unicodedata.normalize("NFKC", combined).casefold()
    matches: list[str] = []
    normalized_matches: list[str] = []
    for keyword in POLITICAL_TOPIC_KEYWORDS:
        normalized_keyword = unicodedata.normalize("NFKC", keyword).casefold()
        if normalized_keyword.isascii():
            matched = bool(
                re.search(
                    rf"(?<![a-z0-9_]){re.escape(normalized_keyword)}(?![a-z0-9_])",
                    normalized,
                )
            )
        else:
            matched = normalized_keyword in normalized
        if not matched or any(
            normalized_keyword in existing for existing in normalized_matches
        ):
            continue
        matches.append(keyword)
        normalized_matches.append(normalized_keyword)
    return matches


def score_and_select_topics(
    candidates: list[dict[str, Any]], messages: Iterable[PromptMessage]
) -> dict[str, Any]:
    evidence = _evidence(messages)
    candidates = list(candidates)
    if len(candidates) < MIN_SELECTED:
        raise TopicSelectionError(
            "TOPIC_CANDIDATES_INSUFFICIENT",
            "无法从真实消息中取得至少一个拥有独立证据的主题",
        )

    metrics: list[dict[str, Any]] = []
    for candidate in candidates[:MAX_CANDIDATES]:
        ids = [message_id for message_id in dict.fromkeys(candidate["message_ids"]) if message_id in evidence]
        if not ids:
            raise TopicSelectionError(
                "TOPIC_CANDIDATES_INVALID",
                f"候选主题“{candidate['title']}”的证据在原始消息中不存在",
            )
        items = [evidence[message_id] for message_id in ids]
        timestamps = [item.timestamp for item in items if item.timestamp is not None]
        duration = 0.0
        if len(timestamps) >= 2:
            duration = max(0.0, (max(timestamps) - min(timestamps)).total_seconds() / 60.0)
        attribution = _participant_attribution(items)
        # 姓名和原话不能采用模型自由输出；统一按 message_id 从当前快照回填。
        verified_quotes = _verified_quotes((), items)
        if not verified_quotes:
            raise TopicSelectionError(
                "TOPIC_CANDIDATES_INVALID",
                f"候选主题“{candidate['title']}”没有可回查的原话证据",
            )
        evidence_dialogue = _evidence_dialogue(items)
        political_keyword_matches = _political_keyword_matches(
            candidate, evidence_dialogue
        )
        dialogue_speaker_count = len(
            {
                str(entry.get("speaker") or "").strip()
                for entry in evidence_dialogue
                if _resolved_participant_name(str(entry.get("speaker") or ""))
            }
        )
        metrics.append(
            {
                **candidate,
                "message_ids": ids,
                "people": attribution["participants"],
                "quotes": verified_quotes,
                "evidence_dialogue": evidence_dialogue,
                "image_eligible": not political_keyword_matches,
                "political_keyword_matches": political_keyword_matches,
                "selection_exclusion_reason": (
                    "POLITICAL_KEYWORD_MATCH" if political_keyword_matches else ""
                ),
                "dialogue_speaker_count": dialogue_speaker_count,
                "evidence_message_count": len(ids),
                **attribution,
                "duration_minutes": round(duration, 1),
                "start_time": min(timestamps).strftime("%Y-%m-%d %H:%M") if timestamps else candidate["start_time"],
                "end_time": max(timestamps).strftime("%Y-%m-%d %H:%M") if timestamps else candidate["end_time"],
            }
        )

    max_messages = max(item["evidence_message_count"] for item in metrics)
    max_participants = max(item["participant_count"] for item in metrics)
    max_duration = max(item["duration_minutes"] for item in metrics)
    scored: list[dict[str, Any]] = []
    for item in metrics:
        scores = {
            "comedy": _score(item["comedy_score"], SCORE_WEIGHTS["comedy"]),
            "group_recognition": _score(
                item["group_recognition_score"], SCORE_WEIGHTS["group_recognition"]
            ),
            "visual": _score(item["visual_score"], SCORE_WEIGHTS["visual"]),
            "discussion": _log_normalized(item["evidence_message_count"], max_messages, SCORE_WEIGHTS["discussion"]),
            "participation": _log_normalized(item["participant_count"], max_participants, SCORE_WEIGHTS["participation"]),
            "continuity": _log_normalized(item["duration_minutes"], max_duration, SCORE_WEIGHTS["continuity"]),
        }
        scores["total"] = round(sum(scores.values()), 1)
        scored.append({**item, "scores": scores})

    scored.sort(
        key=lambda item: (
            -item["scores"]["total"],
            item["start_time"] or "",
            item["topic_id"],
        )
    )
    for rank, item in enumerate(scored, start=1):
        item["rank"] = rank
        item["eligible_rank"] = None
        item["selected"] = False

    eligible_scored = [item for item in scored if item["image_eligible"]]
    selected_ids: list[str] = []
    previous_total: float | None = None
    guaranteed_selected = min(TARGET_SELECTED, len(eligible_scored))
    additional_selection_open = True
    for eligible_rank, item in enumerate(eligible_scored, start=1):
        item["eligible_rank"] = eligible_rank
        selected = eligible_rank <= guaranteed_selected
        if (
            guaranteed_selected < eligible_rank <= MAX_SELECTED
            and additional_selection_open
        ):
            total = item["scores"]["total"]
            gap = (previous_total - total) if previous_total is not None else 0.0
            selected = (
                total >= SELECTION_MIN_SCORE
                and gap < SELECTION_MAX_GAP
                and item["participant_count"] >= 2
                and item["dialogue_speaker_count"] >= 2
            )
            if not selected:
                # 合格候选按分数降序；第一个不满足后，后续候选也不再入选。
                additional_selection_open = False
        item["selected"] = selected
        if selected:
            selected_ids.append(item["topic_id"])
        previous_total = item["scores"]["total"]

    for rank, item in enumerate(scored, start=1):
        item.pop("interestingness_score", None)
        item.pop("comedy_score", None)
        item.pop("group_recognition_score", None)
        item.pop("visual_score", None)

    return {
        "topic_selection_version": TOPIC_SELECTION_VERSION,
        "political_keyword_policy_version": POLITICAL_KEYWORD_POLICY_VERSION,
        "weights": SCORE_WEIGHTS,
        "thresholds": {
            "max_candidates": MAX_CANDIDATES,
            "min_selected": MIN_SELECTED,
            "target_selected": TARGET_SELECTED,
            "max_selected": MAX_SELECTED,
            "high_volume_message_threshold": HIGH_VOLUME_MESSAGE_THRESHOLD,
            "additional_min_score": SELECTION_MIN_SCORE,
            "additional_max_gap": SELECTION_MAX_GAP,
        },
        "candidate_count": len(scored),
        "safe_candidate_count": len(eligible_scored),
        "blocked_candidate_count": len(scored) - len(eligible_scored),
        "blocked_topic_ids": [
            item["topic_id"] for item in scored if not item["image_eligible"]
        ],
        "political_keyword_hits": [
            {
                "topic_id": item["topic_id"],
                "title": item["title"],
                "matched_keywords": item["political_keyword_matches"],
            }
            for item in scored
            if not item["image_eligible"]
        ],
        "selected_count": len(selected_ids),
        "selected_topic_ids": selected_ids,
        "candidates": scored,
    }


def selected_topics_json(selection: dict[str, Any]) -> str:
    selected = [item for item in selection.get("candidates", []) if item.get("selected")]
    if not selected and selection.get("blocked_candidate_count"):
        raise TopicSelectionError(
            "TOPIC_CANDIDATES_POLITICAL",
            "全部候选主题均命中政治关键词，已停止外部生图内容整理",
        )
    if not (MIN_SELECTED <= len(selected) <= MAX_SELECTED):
        raise TopicSelectionError(
            "TOPIC_CANDIDATES_INSUFFICIENT", "最终入选主题数量不在 1～7 范围"
        )
    return json.dumps({"selected_topics": selected}, ensure_ascii=False, separators=(",", ":"))
