"""有证据、喜剧优先的日报候选主题评分与 2～5 个动态选题。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
import re
from typing import Any, Iterable

from app.ai.conversation_segments import ConversationChunk, PromptMessage

TOPIC_SELECTION_VERSION = "2.0"
MAX_CANDIDATES = 8
MIN_SELECTED = 2
MAX_SELECTED = 5
SELECTION_MIN_SCORE = 60.0
SELECTION_MAX_GAP = 15.0

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
事实真实性是准入门槛；通过真实性校验后，好玩程度是第一排序目标。
内容充足时输出 8 个候选，证据不足时允许少于 8 个；共享同一核心事实、里程碑或结论的相似话题必须合并，
不得为了凑数把一个事件拆成“发起/回应”或重复角度。
comedy_score 为 0～40，group_recognition_score 为 0～20，visual_score 为 0～20。
comedy_angle 说明真实笑点，visual_gag 说明不改变事实的视觉笑点，并给出简短 score_reason。
为避免响应截断，标题、摘要、人物和评分理由必须简洁；每个候选的 message_ids 最多保留 100 条有效证据。"""

_CANDIDATE_SCHEMA = """返回结构：
{
  "candidates": [
    {
      "topic_id": "topic-01",
      "title": "主题短标题",
      "summary": "基于证据的简要描述",
      "people": ["真实参与者"],
      "quotes": ["1-3 条真实原话或忠实缩写"],
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
    sender_key: str
    sender_name: str
    timestamp: datetime | None


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
        "请语义合并后形成当天最多 8 个候选主题，不得添加卡片之外的事实。\n\n"
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
    for item in messages:
        if not item.message_id:
            continue
        result[item.message_id] = TopicEvidence(
            message_id=item.message_id,
            sender_key=item.sender_id or item.sender_name or "(未知)",
            sender_name=item.sender_name or "(未知)",
            timestamp=item.timestamp,
        )
    return result


def _log_normalized(value: float, maximum: float, weight: float) -> float:
    if value <= 0 or maximum <= 0:
        return 0.0
    return round(weight * math.log1p(value) / math.log1p(maximum), 1)


def score_and_select_topics(
    candidates: list[dict[str, Any]], messages: Iterable[PromptMessage]
) -> dict[str, Any]:
    evidence = _evidence(messages)
    candidates = list(candidates)
    if len(candidates) < MIN_SELECTED:
        raise TopicSelectionError(
            "TOPIC_CANDIDATES_INSUFFICIENT",
            "无法从真实消息中取得至少两个拥有独立证据的主题",
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
        participant_names = sorted({item.sender_name for item in items if item.sender_name})
        participant_keys = {item.sender_key for item in items if item.sender_key}
        duration = 0.0
        if len(timestamps) >= 2:
            duration = max(0.0, (max(timestamps) - min(timestamps)).total_seconds() / 60.0)
        metrics.append(
            {
                **candidate,
                "message_ids": ids,
                "evidence_message_count": len(ids),
                "participant_count": len(participant_keys),
                "participants": participant_names,
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

    scored.sort(key=lambda item: (-item["scores"]["total"], item["start_time"] or "", item["topic_id"]))
    selected_ids: list[str] = []
    previous_total: float | None = None
    for rank, item in enumerate(scored, start=1):
        selected = rank <= MIN_SELECTED
        if MIN_SELECTED < rank <= MAX_SELECTED:
            total = item["scores"]["total"]
            gap = (previous_total - total) if previous_total is not None else 0.0
            selected = total >= SELECTION_MIN_SCORE and gap < SELECTION_MAX_GAP
            if not selected:
                # 分数降序；第一个不满足后，后续候选也不再入选。
                for tail in scored[rank - 1 :]:
                    tail["selected"] = False
                break
        item["rank"] = rank
        item["selected"] = selected
        if selected:
            selected_ids.append(item["topic_id"])
        previous_total = item["scores"]["total"]

    for rank, item in enumerate(scored, start=1):
        item.setdefault("rank", rank)
        item.setdefault("selected", False)
        item.pop("interestingness_score", None)
        item.pop("comedy_score", None)
        item.pop("group_recognition_score", None)
        item.pop("visual_score", None)

    return {
        "topic_selection_version": TOPIC_SELECTION_VERSION,
        "weights": SCORE_WEIGHTS,
        "thresholds": {
            "max_candidates": MAX_CANDIDATES,
            "min_selected": MIN_SELECTED,
            "max_selected": MAX_SELECTED,
            "additional_min_score": SELECTION_MIN_SCORE,
            "additional_max_gap": SELECTION_MAX_GAP,
        },
        "candidate_count": len(scored),
        "selected_count": len(selected_ids),
        "selected_topic_ids": selected_ids,
        "candidates": scored,
    }


def selected_topics_json(selection: dict[str, Any]) -> str:
    selected = [item for item in selection.get("candidates", []) if item.get("selected")]
    if not (MIN_SELECTED <= len(selected) <= MAX_SELECTED):
        raise TopicSelectionError("TOPIC_CANDIDATES_INSUFFICIENT", "最终入选主题数量不在 2～5 范围")
    return json.dumps({"selected_topics": selected}, ensure_ascii=False, separators=(",", ":"))
