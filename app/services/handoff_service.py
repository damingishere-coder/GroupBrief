"""本地文件输出 + V2 Handoff。

输出结构（文档 §17/§18）：
output/YYYY-MM-DD/{safe_group}/
  ├─ ranking.txt
  ├─ image_prompt.txt
  ├─ meta.json
  ├─ normalized_messages.json
  └─ handoff.json

V1：poster_file=null, status="prompt_ready"
V2：Codex 生图后更新 poster_file 与 status。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import Group
from app.scheduler.calendar_rules import ReportWindow
from app.services.message_normalizer import NormalizedMessage
from app.services.ranking_service import RankingResult

logger = get_logger("app")

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def safe_dir_name(name: str, fallback: str = "group") -> str:
    cleaned = _INVALID_CHARS.sub("-", name).strip("-")
    return cleaned[:60] or fallback


class HandoffService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def save_outputs(
        self,
        group: Group,
        window: ReportWindow,
        ranking: RankingResult,
        prompt_text: str,
        normalized: list[NormalizedMessage],
        provider: str,
    ) -> Path:
        base_dir = self.settings.output_dir / window.report_date.isoformat()
        base = group.display_name or group.wechat_group_name or f"group-{group.id}"
        group_dir = base_dir / safe_dir_name(base)
        group_dir.mkdir(parents=True, exist_ok=True)

        ranking_file = group_dir / "ranking.txt"
        ranking_file.write_text(ranking.render(), encoding="utf-8")

        prompt_file = group_dir / "image_prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        meta = {
            "report_date": window.report_date.isoformat(),
            "group_id": group.wechat_group_id or str(group.id),
            "group_name": group.display_name or group.wechat_group_name,
            "range_start": ranking.range_start,
            "range_end": ranking.range_end,
            "provider": provider,
            "message_count": ranking.total_messages,
            "speaker_count": ranking.speaker_count,
            "generated_at": datetime.now().isoformat(),
        }
        (group_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        normalized_file = group_dir / "normalized_messages.json"
        (normalized_file).write_text(
            json.dumps([m.to_dict() for m in normalized], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

        handoff = {
            "version": 1,
            "date": window.report_date.isoformat(),
            "group_id": group.wechat_group_id or str(group.id),
            "group_name": group.display_name or group.wechat_group_name,
            "ranking_file": ranking_file.name,
            "prompt_file": prompt_file.name,
            "poster_file": None,  # V2 使用
            "status": "prompt_ready",  # V2: poster_ready / sent
        }
        (group_dir / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info("输出已保存：%s", group_dir)
        return group_dir

    def list_output_dates(self) -> list[str]:
        if not self.settings.output_dir.exists():
            return []
        return sorted(
            (d.name for d in self.settings.output_dir.iterdir() if d.is_dir()),
            reverse=True,
        )

    def list_group_outputs(self, report_date: str) -> list[dict]:
        day_dir = self.settings.output_dir / report_date
        if not day_dir.exists():
            return []
        result = []
        for group_dir in sorted(day_dir.iterdir()):
            if not group_dir.is_dir():
                continue
            handoff_file = group_dir / "handoff.json"
            handoff = {}
            if handoff_file.exists():
                handoff = json.loads(handoff_file.read_text(encoding="utf-8"))
            result.append(
                {
                    "date": report_date,
                    "directory": group_dir.name,
                    "path": str(group_dir),
                    "handoff": handoff,
                    "files": sorted(p.name for p in group_dir.iterdir()),
                }
            )
        return result
