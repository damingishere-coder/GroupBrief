"""Offline workday decisions, backed by versioned holiday-cn government-calendar copies."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from app.config.settings import PROJECT_ROOT

BUNDLED = Path(__file__).with_name("calendars")
CACHE = Path(os.environ.get("GROUPBRIEF_CALENDAR_DIR", str(PROJECT_ROOT / "data" / "work_calendar")))
SOURCE = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"


class CalendarUnavailableError(ValueError):
    pass


def validate_calendar(payload: dict, year: int) -> dict:
    if not isinstance(payload, dict) or type(payload.get("year")) is not int or payload["year"] != year:
        raise CalendarUnavailableError(f"{year} 年日历年份不匹配")
    papers = payload.get("papers")
    if not isinstance(papers, list) or not papers or any(
        not isinstance(url, str) or urlparse(url).scheme != "https"
        or not (urlparse(url).hostname or "").endswith(".gov.cn") for url in papers
    ):
        raise CalendarUnavailableError(f"{year} 年日历缺少政府公告出处")
    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise CalendarUnavailableError(f"{year} 年日历没有节假日数据")
    seen = set()
    for item in days:
        try:
            day = date.fromisoformat(item["date"])
            valid = day.year in (year - 1, year) and type(item["isOffDay"]) is bool and bool(item["name"])
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid or item["date"] in seen:
            raise CalendarUnavailableError(f"{year} 年日历日期无效或重复")
        seen.add(item["date"])
    return {"year": year, "papers": papers, "days": sorted(days, key=lambda item: item["date"])}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class ChinaCalendar:
    def __init__(self, cache_dir: Path | None = None, bundled_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else CACHE
        self.bundled_dir = Path(bundled_dir) if bundled_dir is not None else BUNDLED
        self.versions: dict[int, str] = {}
        self._years: dict[int, dict] = {}

    def _load(self, year: int, *, required: bool = True) -> dict | None:
        if year in self._years:
            return self._years[year]
        path = self.cache_dir / f"{year}.json"
        if not path.exists():
            path = self.bundled_dir / f"{year}.json"
        if not path.exists():
            if not required:
                return None
            raise CalendarUnavailableError(f"缺少 {year} 年中国工作日日历，已暂停自动任务")
        try:
            payload = validate_calendar(json.loads(path.read_text(encoding="utf-8")), year)
        except (OSError, ValueError, TypeError) as exc:
            raise CalendarUnavailableError(f"{year} 年日历不可用：{exc}") from exc
        self._years[year] = payload
        self.versions[year] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return payload

    def is_workday(self, day: date) -> bool:
        current = self._load(day.year)
        # A following-year notice may assign December makeup workdays.
        following = self._load(day.year + 1) if day.month == 12 else None
        entries = [item["isOffDay"] for payload in (current, following) if payload
                   for item in payload["days"] if item["date"] == day.isoformat()]
        if len(set(entries)) > 1:
            raise CalendarUnavailableError(f"{day} 的工作日日历存在冲突")
        return not entries[0] if entries else day.weekday() < 5

    def previous_workday(self, day: date) -> date:
        for offset in range(1, 367):
            candidate = day - timedelta(days=offset)
            if self.is_workday(candidate):
                return candidate
        raise CalendarUnavailableError("一年内未找到前一个工作日")

    def override(self, run_date: date, group_id: int | None) -> dict | None:
        path = self.cache_dir / "overrides.json"
        if not path.exists():
            return None
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))["overrides"]
            if not isinstance(rows, list):
                raise ValueError("overrides 必须是列表")
            matches = []
            for row in rows:
                target = date.fromisoformat(row["run_date"])
                start, end = date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])
                if not row["id"] or not start <= end < target or not isinstance(row["group_ids"], list):
                    raise ValueError("临时安排范围无效")
                if target == run_date and group_id is not None and group_id in row["group_ids"]:
                    matches.append(row)
            if len(matches) > 1:
                raise ValueError("同一群同一天存在重复临时安排")
            return matches[0] if matches else None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CalendarUnavailableError(f"临时安排不可用：{exc}") from exc

    @property
    def version(self) -> str:
        return ";".join(f"{year}:{digest}" for year, digest in sorted(self.versions.items()))

    def status(self, year: int) -> dict:
        checked_at = ""
        try:
            checked_at = json.loads((self.cache_dir / "refresh_status.json").read_text(encoding="utf-8")).get("checked_at", "")
        except (OSError, ValueError, AttributeError):
            pass
        try:
            data = self._load(year)
            return {"year": year, "source": "holiday-cn（国务院公告结构化副本）", "papers": data["papers"],
                    "version": self.version, "checked_at": checked_at, "error": ""}
        except CalendarUnavailableError as exc:
            return {"year": year, "checked_at": checked_at, "error": str(exc)}


def refresh_calendars(*, cache_dir: Path | None = None, today: date | None = None, client=None) -> dict:
    """Bounded network refresh; invalid responses never replace a usable offline copy."""
    today = today or date.today()
    root = Path(cache_dir) if cache_dir is not None else CACHE
    results = {}
    owns_client = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                response = client.get(SOURCE.format(year=year))
                response.raise_for_status()
                if len(response.content) > 256_000:
                    raise ValueError("日历文件过大")
                data = validate_calendar(response.json(), year)
                digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                # Preserve the exact calendar behind future frozen task manifests.
                atomic_json(root / "versions" / f"{year}-{digest}.json", data)
                data["fetched_at"] = datetime.now(timezone.utc).isoformat()
                data["source"] = SOURCE.format(year=year)
                atomic_json(root / f"{year}.json", data)
                results[str(year)] = "updated"
            except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
                results[str(year)] = f"cached_or_unavailable: {str(exc)[:200]}"
        result = {"checked_at": datetime.now(timezone.utc).isoformat(), "years": results}
        atomic_json(root / "refresh_status.json", result)
        return result
    finally:
        if owns_client:
            client.close()
