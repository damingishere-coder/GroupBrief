"""V2 运行状态存储：output/<群名称>/<日期>/run.json。

run.json 是每个群每次运行的唯一状态文件（路线文档 §十）。
状态机：PENDING → DATA_READY → RANKING_READY → PROMPT_READY →
IMAGE_READY → READY_TO_SEND → SENT / FAILED。

同时统一管理该群该日期的输出文件命名与目录。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.services.handoff_service import safe_dir_name
from app.v2.constants import (
    FILE_IMAGE,
    FILE_MESSAGES,
    FILE_PROMPT,
    FILE_RANKING_JSON,
    FILE_RANKING_TXT,
    FILE_RUN,
    PENDING,
)


class RunStore:
    def __init__(self, output_root: Path | str):
        self.root = Path(output_root)

    # ---------- 路径 ----------

    def group_dir(self, group_name: str, run_date: str) -> Path:
        return self.root / safe_dir_name(group_name) / run_date

    def run_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RUN

    # 输出文件绝对路径
    def messages_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_MESSAGES

    def ranking_json_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RANKING_JSON

    def ranking_txt_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_RANKING_TXT

    def prompt_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_PROMPT

    def image_path(self, group_name: str, run_date: str) -> Path:
        return self.group_dir(group_name, run_date) / FILE_IMAGE

    # ---------- run.json ----------

    def load_run(self, group_name: str, run_date: str) -> dict:
        path = self.run_path(group_name, run_date)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {"group_name": group_name, "run_date": run_date, "status": PENDING}

    def save_run(self, group_name: str, run_date: str, data: dict) -> dict:
        path = self.run_path(group_name, run_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        data.setdefault("group_name", group_name)
        data["run_date"] = run_date
        data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    def update(self, group_name: str, run_date: str, **fields) -> dict:
        """加载 → 合并字段 → 保存，返回最新 run。"""
        data = self.load_run(group_name, run_date)
        data.update(fields)
        return self.save_run(group_name, run_date, data)

    def list_runs(self, run_date: str | None = None) -> list[dict]:
        """列出全部已存在的 run（可按日期过滤）。

        run.json 位于 group_dir/<日期>/run.json；未指定日期时遍历每个群的
        所有日期子目录。
        """
        runs: list[dict] = []
        if not self.root.exists():
            return runs
        for group_dir in self.root.iterdir():
            if not group_dir.is_dir():
                continue
            if run_date:
                candidates = [group_dir / run_date]
            else:
                candidates = [d for d in group_dir.iterdir() if d.is_dir()]
            for d in candidates:
                run_path = d / FILE_RUN
                if run_path.exists():
                    try:
                        runs.append(json.loads(run_path.read_text(encoding="utf-8")))
                    except (json.JSONDecodeError, OSError):
                        continue
        runs.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return runs
