"""独立运行一次 GroupBrief Codex 维修队列。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config.settings import get_settings
from app.repair.controller import RepairController
from app.repair.events import capture_persisted_incidents


def main() -> int:
    settings = get_settings()
    capture_persisted_incidents(settings)
    result = RepairController(settings).run_once()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") in {"disabled", "not_run", "pr_created"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
