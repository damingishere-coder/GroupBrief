"""发言人身份与展示名的确定性聚合。

统计身份优先使用 sender_id；只有缺少 ID 时才回退到展示名。展示名只负责
呈现，不参与判断是否为同一个人。这样可以同时保证：

- 同一 ID 改昵称仍算一人；
- 不同 ID 即使同名仍算多人；
- 缺少可靠姓名时仍能为每个身份生成稳定、互不混淆的名称。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import unicodedata
from typing import Iterable


IdentityKey = tuple[str, str]


@dataclass(frozen=True)
class SpeakerStat:
    key: IdentityKey
    name: str
    count: int
    first_index: int


def speaker_identity_key(sender_id: object, sender_name: object) -> IdentityKey | None:
    """返回稳定身份键；ID 存在时姓名不会影响身份。"""
    identifier = str(sender_id or "").strip()
    if identifier:
        return ("id", identifier)
    name = str(sender_name or "").strip()
    if name:
        return ("name", name)
    return None


def anonymous_speaker_name(key: IdentityKey) -> str:
    digest = hashlib.sha256(f"{key[0]}:{key[1]}".encode("utf-8")).hexdigest()[:8]
    return f"未命名成员-{digest}"


def speaker_name_sort_key(name: str) -> tuple[str, str]:
    """同分时忽略前导 Emoji/装饰符，保持跨引擎一致的稳定排序。"""
    visible_core = str(name or "").lstrip()
    while visible_core and unicodedata.category(visible_core[0]).startswith(("P", "S", "Z")):
        visible_core = visible_core[1:].lstrip()
    return ((visible_core or str(name or "")).casefold(), str(name or ""))


def _usable_name(name: str, key: IdentityKey, *, trusted: bool = False) -> bool:
    normalized = name.strip()
    if not normalized or normalized.casefold() in {"none", "null", "(未知)", "未知"}:
        return False
    return trusted or key[0] != "id" or normalized.casefold() != key[1].casefold()


def build_speaker_stats(
    records: Iterable[tuple[object, object] | tuple[object, object, object]],
) -> list[SpeakerStat]:
    """按身份聚合记录，并为重名身份生成不重复的确定性展示名。

    第三个可选值表示名称来自可信解析源。联系人名称即使仅与微信 ID
    大小写不同也应保留；未提供该标志的旧调用继续执行原有匿名保护。
    """
    aggregated: dict[IdentityKey, dict[str, object]] = {}
    for index, record in enumerate(records):
        sender_id, sender_name = record[0], record[1]
        trusted = bool(record[2]) if len(record) > 2 else False
        key = speaker_identity_key(sender_id, sender_name)
        if key is None:
            continue
        current = aggregated.get(key)
        if current is None:
            current = {
                "count": 0,
                "first_index": index,
                "names": Counter(),
                "name_first_index": {},
            }
            aggregated[key] = current
        current["count"] = int(current["count"]) + 1
        name = str(sender_name or "").strip()
        if _usable_name(name, key, trusted=trusted):
            names = current["names"]
            name_first_index = current["name_first_index"]
            assert isinstance(names, Counter)
            assert isinstance(name_first_index, dict)
            names[name] += 1
            name_first_index.setdefault(name, index)

    base_names: dict[IdentityKey, str] = {}
    for key, current in aggregated.items():
        names = current["names"]
        name_first_index = current["name_first_index"]
        assert isinstance(names, Counter)
        assert isinstance(name_first_index, dict)
        if names:
            base_names[key] = min(
                names,
                key=lambda name: (
                    -names[name],
                    name_first_index[name],
                    speaker_name_sort_key(name),
                ),
            )
        else:
            base_names[key] = anonymous_speaker_name(key)

    duplicate_groups: dict[str, list[IdentityKey]] = {}
    for key, name in base_names.items():
        duplicate_groups.setdefault(name.casefold(), []).append(key)

    display_names = dict(base_names)
    for duplicate_keys in duplicate_groups.values():
        if len(duplicate_keys) <= 1:
            continue
        for number, key in enumerate(sorted(duplicate_keys), start=1):
            display_names[key] = f"{base_names[key]}（同名 {number}）"

    return [
        SpeakerStat(
            key=key,
            name=display_names[key],
            count=int(current["count"]),
            first_index=int(current["first_index"]),
        )
        for key, current in sorted(
            aggregated.items(), key=lambda item: (int(item[1]["first_index"]), item[0])
        )
    ]
