"""Build deterministic, message-scoped speaker attribution for image prompts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable

from app.providers.history.wechat_data_analysis import (
    _sanitize_sender_name,
    _usable_sender_name,
)
from app.services.speaker_identity import speaker_identity_key


@dataclass(frozen=True)
class AttributionName:
    """The display name to use for one specific chat message."""

    display_name: str
    source: str


@dataclass(frozen=True)
class AttributionContract:
    """Snapshot hashes and per-message names used by the summary pipeline."""

    names: tuple[AttributionName, ...]
    message_snapshot_sha256: str
    speaker_fingerprint: str


def _field(message: object, name: str) -> Any:
    if isinstance(message, dict):
        return message.get(name)
    return getattr(message, name, None)


def _timestamp_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _snapshot_record(message: object) -> dict[str, str]:
    return {
        "message_id": str(_field(message, "message_id") or ""),
        "group_id": str(_field(message, "group_id") or ""),
        "group_name": str(_field(message, "group_name") or ""),
        "sender_id": str(_field(message, "sender_id") or ""),
        "sender_name": str(_field(message, "sender_name") or ""),
        "timestamp": _timestamp_text(_field(message, "timestamp")),
        "message_type": str(_field(message, "message_type") or "text"),
        "content": str(_field(message, "content") or ""),
        "upstream_sender_name": str(
            _field(message, "upstream_sender_name") or ""
        ),
        "sender_name_source": str(_field(message, "sender_name_source") or ""),
    }


def _sha256(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolved_fallback(message: object, sender_id: str) -> str:
    resolved = _sanitize_sender_name(_field(message, "sender_name"))
    if resolved and resolved.casefold() not in {"none", "null", "(未知)", "未知"}:
        return resolved
    identity = speaker_identity_key(sender_id, resolved)
    if identity is None:
        return "(未知)"
    digest = hashlib.sha256(f"{identity[0]}:{identity[1]}".encode("utf-8")).hexdigest()[:8]
    return f"未命名成员-{digest}"


def resolve_attribution_names(messages: Iterable[object]) -> tuple[AttributionName, ...]:
    """Prefer the valid chat-time name; fall back only when it is unsafe.

    The choice is message-scoped. A member who changed their group card during
    the day keeps the name that accompanied each individual message.
    """

    rows = list(messages)
    sender_ids = {
        str(_field(message, "sender_id") or "").strip()
        for message in rows
        if str(_field(message, "sender_id") or "").strip()
    }
    sender_ids_casefold = {sender_id.casefold() for sender_id in sender_ids}
    upstream_identities: dict[str, set[str]] = {}
    normalized_upstream: list[str] = []
    for message in rows:
        sender_id = str(_field(message, "sender_id") or "").strip()
        upstream = _sanitize_sender_name(_field(message, "upstream_sender_name"))
        normalized_upstream.append(upstream)
        if sender_id and _usable_sender_name(upstream, sender_id):
            upstream_identities.setdefault(upstream.casefold(), set()).add(
                sender_id.casefold()
            )

    base: list[AttributionName] = []
    identities: list[tuple[str, str] | None] = []
    for message, upstream in zip(rows, normalized_upstream):
        sender_id = str(_field(message, "sender_id") or "").strip()
        usable_upstream = bool(
            sender_id
            and _usable_sender_name(upstream, sender_id)
            and upstream.casefold()
            not in (sender_ids_casefold - {sender_id.casefold()})
            and len(upstream_identities.get(upstream.casefold(), set())) == 1
        )
        if usable_upstream:
            name = upstream
            source = "upstream_sender_name"
        else:
            name = _resolved_fallback(message, sender_id)
            source = str(_field(message, "sender_name_source") or "resolved")
        base.append(AttributionName(name, source))
        identities.append(speaker_identity_key(sender_id.casefold(), name))

    # If the fallback still leaves two identities with the same label, keep the
    # existing stable suffix convention without changing names that only vary
    # over time for the same sender_id.
    duplicate_identities: dict[str, set[tuple[str, str]]] = {}
    for attribution, identity in zip(base, identities):
        if identity is not None:
            duplicate_identities.setdefault(
                attribution.display_name.casefold(), set()
            ).add(identity)
    suffixes: dict[tuple[str, tuple[str, str]], int] = {}
    for normalized_name, identity_set in duplicate_identities.items():
        if len(identity_set) <= 1:
            continue
        for number, identity in enumerate(sorted(identity_set), start=1):
            suffixes[(normalized_name, identity)] = number

    result: list[AttributionName] = []
    for attribution, identity in zip(base, identities):
        number = (
            suffixes.get((attribution.display_name.casefold(), identity))
            if identity is not None
            else None
        )
        if number is None:
            result.append(attribution)
        else:
            result.append(
                AttributionName(
                    f"{attribution.display_name}（同名 {number}）",
                    attribution.source,
                )
            )
    return tuple(result)


def build_attribution_contract(messages: Iterable[object]) -> AttributionContract:
    rows = list(messages)
    names = resolve_attribution_names(rows)
    snapshot_records = [_snapshot_record(message) for message in rows]
    speaker_records = [
        {
            "message_id": record["message_id"],
            "sender_id": record["sender_id"],
            "display_name": attribution.display_name,
            "display_name_source": attribution.source,
            "upstream_sender_name": record["upstream_sender_name"],
            "sender_name_source": record["sender_name_source"],
        }
        for record, attribution in zip(snapshot_records, names)
    ]
    return AttributionContract(
        names=names,
        message_snapshot_sha256=_sha256(snapshot_records),
        speaker_fingerprint=_sha256(speaker_records),
    )
