from __future__ import annotations

import threading
import time

from app.config.settings import Settings
from app.db.models import Group
from app.pipeline.daily_pipeline import DailyPipeline
from app.v2.run_store import RunStore


def test_snapshot_prompt_batch_runs_in_parallel_and_preserves_input_order(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        generation_group_concurrency=1,
    )
    store = RunStore(tmp_path / "output")
    groups = {
        group_id: Group(
            id=group_id,
            display_name=f"群{index}",
            wechat_group_id=f"wx-{index}",
        )
        for index, group_id in enumerate(range(23, 29), start=1)
    }
    for group in groups.values():
        store.save_run(
            group.display_name,
            "2026-08-25",
            {
                "group_name": group.display_name,
                "group_id": group.id,
                "wechat_group_id": group.wechat_group_id,
                "status": "SENT",
            },
        )

    pipeline = DailyPipeline(settings=settings, store=store, dry_run=True)
    monkeypatch.setattr(pipeline, "_get_group", lambda group_id: groups.get(group_id))
    guard = threading.Lock()
    active = 0
    maximum = 0

    def fake_rebuild(group_id, run_date, *, acquire_lock=True):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.08)
        with guard:
            active -= 1
        return {"group_name": groups[group_id].display_name, "status": "prompt_ready"}

    monkeypatch.setattr(pipeline, "rebuild_prompt_from_snapshot", fake_rebuild)
    targets = [
        (group.id, group.wechat_group_id, "2026-08-25")
        for group in groups.values()
    ]
    results = pipeline.rebuild_prompts_from_snapshots(
        targets,
        acquire_lock=False,
    )

    assert maximum == 6
    assert [item["group_id"] for item in results] == list(range(23, 29))
    assert [item["group_name"] for item in results] == [f"群{index}" for index in range(1, 7)]


def test_snapshot_prompt_batch_rejects_group_identity_mismatch(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, output_dir=tmp_path / "output")
    store = RunStore(tmp_path / "output")
    group = Group(id=23, display_name="群A", wechat_group_id="wx-a")
    store.save_run(
        "群A",
        "2026-08-25",
        {
            "group_name": "群A",
            "group_id": 23,
            "wechat_group_id": "wx-a",
            "status": "SENT",
        },
    )
    pipeline = DailyPipeline(settings=settings, store=store, dry_run=True)
    monkeypatch.setattr(pipeline, "_get_group", lambda _group_id: group)

    result = pipeline.rebuild_prompts_from_snapshots(
        [(23, "wrong-wechat-id", "2026-08-25")],
        acquire_lock=False,
    )[0]

    assert result["status"] == "failed"
    assert result["error_type"] == "GROUP_IDENTITY_MISMATCH"
