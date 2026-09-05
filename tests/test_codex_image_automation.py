from __future__ import annotations

import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace
from pathlib import Path

from PIL import Image

from app.v2.constants import FAILED, IMAGE_GENERATION_FAILED, PROMPT_READY, READY_TO_SEND
from app.v2.run_store import RunStore
from scripts import codex_image_automation as automation
from scripts.codex_image_automation import adopt_image, begin_task, collect_pending


_png_buffer = BytesIO()
Image.new("RGBA", (2, 2), (18, 52, 86, 255)).save(_png_buffer, format="PNG")
PNG_BYTES = _png_buffer.getvalue()


def _make_run(store: RunStore, group: str = "测试群", date: str = "2026-08-20", **fields) -> None:
    payload = {
        "group_name": group,
        "run_date": date,
        "status": PROMPT_READY,
        "image_enabled": True,
        **fields,
    }
    store.save_run(group, date, payload)
    store.prompt_path(group, date).write_text("生成一张测试日报图片", encoding="utf-8")


def _patch_group_db(tmp_path: Path, monkeypatch, groups: list[tuple[int, str, str, int]]) -> Path:
    """把自动化脚本的只读群配置查询指向临时 SQLite。"""
    db_path = tmp_path / "groups.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE groups ("
            "id INTEGER PRIMARY KEY, display_name TEXT, "
            "wechat_group_name TEXT, image_enabled BOOLEAN NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO groups(id, display_name, wechat_group_name, image_enabled) VALUES (?, ?, ?, ?)",
            groups,
        )
    monkeypatch.setattr(
        automation,
        "get_settings",
        lambda: SimpleNamespace(db_path=db_path, app_timezone="Asia/Shanghai"),
    )
    return db_path


def test_collect_pending_only_returns_real_image_tasks(tmp_path: Path):
    store = RunStore(tmp_path / "output")
    _make_run(store)
    _make_run(store, group="关闭图片群", image_enabled=False)
    _make_run(
        store,
        group="旧失败群",
        status=FAILED,
        error_type=IMAGE_GENERATION_FAILED,
        image_error="旧 CLI 调用失败",
    )

    tasks = collect_pending(store, "2026-08-20", 5)

    assert [task["group_name"] for task in tasks] == ["旧失败群", "测试群"]
    assert all(task["prompt_chars"] > 0 for task in tasks)


def test_collect_pending_filters_report_date_and_keeps_real_run_date(tmp_path: Path, monkeypatch):
    _patch_group_db(tmp_path, monkeypatch, [])
    store = RunStore(tmp_path / "output")
    _make_run(
        store,
        group="报告日期测试群",
        date="2026-08-21",
        period_end="2026-08-20 23:59:59",
    )

    tasks = collect_pending(store, report_date="2026-08-20", limit=5)

    assert len(tasks) == 1
    assert tasks[0]["run_date"] == "2026-08-21"
    assert tasks[0]["report_date"] == "2026-08-20"
    assert collect_pending(store, report_date="2026-08-19", limit=5) == []


def test_current_db_image_switch_true_overrides_run_snapshot_for_all_steps(tmp_path: Path, monkeypatch):
    _patch_group_db(tmp_path, monkeypatch, [(7, "数据库开关测试群", "数据库开关测试群", 1)])
    store = RunStore(tmp_path / "output")
    _make_run(
        store,
        group="数据库开关测试群",
        date="2026-08-21",
        group_id="7",
        period_end="2026-08-20 23:59:59",
        image_enabled=False,
    )
    generated_dir = tmp_path / "generated_images"
    generated_dir.mkdir()

    pending = collect_pending(store, report_date="2026-08-20", limit=5)
    begin = begin_task(store, generated_dir, "数据库开关测试群", "2026-08-21")
    new_image = generated_dir / "new.png"
    new_image.write_bytes(PNG_BYTES)
    adopted = adopt_image(store, "数据库开关测试群", "2026-08-21")

    assert [task["run_date"] for task in pending] == ["2026-08-21"]
    assert begin["ok"] is True
    assert adopted["ok"] is True


def test_current_db_image_switch_false_filters_run_snapshot_true(tmp_path: Path, monkeypatch):
    _patch_group_db(tmp_path, monkeypatch, [(8, "关闭开关测试群", "关闭开关测试群", 0)])
    store = RunStore(tmp_path / "output")
    _make_run(
        store,
        group="关闭开关测试群",
        date="2026-08-21",
        group_id="8",
        period_end="2026-08-20 23:59:59",
        image_enabled=True,
    )

    assert collect_pending(store, report_date="2026-08-20", limit=5) == []


def test_begin_and_adopt_new_generated_png(tmp_path: Path):
    store = RunStore(tmp_path / "output")
    generated_dir = tmp_path / "generated_images"
    generated_dir.mkdir()
    old_image = generated_dir / "old.png"
    old_image.write_bytes(PNG_BYTES)
    _make_run(store)

    begin = begin_task(store, generated_dir, "测试群", "2026-08-20")
    marker = Path(begin["marker_path"])
    assert marker.is_file()
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert str(old_image.resolve()) in marker_payload["before"]

    new_image = generated_dir / "new.png"
    new_image.write_bytes(PNG_BYTES + b"-new")
    result = adopt_image(store, "测试群", "2026-08-20")

    assert result["ok"] is True
    assert result["status"] == READY_TO_SEND
    assert store.image_path("测试群", "2026-08-20").read_bytes() == new_image.read_bytes()
    assert not marker.exists()
    run = store.load_run("测试群", "2026-08-20")
    assert run["image_status"] == "success"
    assert run["imagegen_ms"] >= 0
    assert run["stage_timings"]["imagegen_ms"] == run["imagegen_ms"]
    assert run["image_size_bytes"] == len(new_image.read_bytes())
    assert run["image_generated_at"]


def test_adopt_requires_explicit_source_when_multiple_new_pngs(tmp_path: Path):
    store = RunStore(tmp_path / "output")
    generated_dir = tmp_path / "generated_images"
    generated_dir.mkdir()
    _make_run(store)
    begin_task(store, generated_dir, "测试群", "2026-08-20")
    (generated_dir / "expected.png").write_bytes(PNG_BYTES + b"-expected")
    (generated_dir / "unrelated.png").write_bytes(PNG_BYTES + b"-unrelated")

    try:
        adopt_image(store, "测试群", "2026-08-20")
    except ValueError as exc:
        assert "2 个新增 PNG" in str(exc)
        assert "--source" in str(exc)
    else:
        raise AssertionError("多个新增 PNG 时不应自动猜测图片归属")

    assert not store.image_path("测试群", "2026-08-20").exists()


def test_adopt_rejects_source_outside_generated_dir(tmp_path: Path):
    store = RunStore(tmp_path / "output")
    generated_dir = tmp_path / "generated_images"
    generated_dir.mkdir()
    _make_run(store)
    begin_task(store, generated_dir, "测试群", "2026-08-20")
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)

    try:
        adopt_image(store, "测试群", "2026-08-20", outside)
    except ValueError as exc:
        assert "$CODEX_HOME/generated_images" in str(exc)
    else:
        raise AssertionError("外部图片路径应被拒绝")


def test_desktop_regeneration_fallback_replaces_old_image_and_requires_review(tmp_path: Path, monkeypatch):
    _patch_group_db(tmp_path, monkeypatch, [])
    store = RunStore(tmp_path / "output")
    generated_dir = tmp_path / "generated_images"
    generated_dir.mkdir()
    _make_run(
        store,
        group="旧日期重生图群",
        date="2026-08-10",
        status="SENT",
        period_end="2026-08-09 23:59:59",
        desktop_regen_requested=True,
        image_regen_status="fallback_queued",
        send_hold=True,
    )
    old = store.image_path("旧日期重生图群", "2026-08-10")
    old.write_bytes(PNG_BYTES + b"-old")

    # 即使默认查询的是另一个报告日，显式 Desktop 回退任务也必须被取出。
    tasks = collect_pending(store, report_date="2026-08-20", limit=5)
    assert len(tasks) == 1
    assert tasks[0]["regeneration"] is True

    begin = begin_task(store, generated_dir, "旧日期重生图群", "2026-08-10")
    assert begin["regeneration"] is True
    new = generated_dir / "regenerated.png"
    new.write_bytes(PNG_BYTES + b"-new")
    result = adopt_image(store, "旧日期重生图群", "2026-08-10")
    run = store.load_run("旧日期重生图群", "2026-08-10")

    assert result["status"] == "SENT"
    assert store.image_path("旧日期重生图群", "2026-08-10").read_bytes() == new.read_bytes()
    assert store.previous_image_path("旧日期重生图群", "2026-08-10").read_bytes() == PNG_BYTES + b"-old"
    assert run["image_regen_status"] == "ready_for_review"
    assert run["desktop_regen_requested"] is False
    assert run["send_hold"] is True
    assert run["needs_manual_send"] is True


def test_explicit_adopt_preserves_timing_audits_hash_and_syncs_scheduler(tmp_path: Path):
    store = RunStore(tmp_path / "output")
    generated_dir = tmp_path / "generated_images"
    source = generated_dir / "execution-approved" / "final.png"
    source.parent.mkdir(parents=True)
    _make_run(
        store,
        status=FAILED,
        failed_stage="image",
        error_type=IMAGE_GENERATION_FAILED,
        image_error="codex 超时",
        imagegen_ms=601137,
        stage_timings={"imagegen_ms": 601137},
    )
    begin_task(store, generated_dir, "测试群", "2026-08-20")
    source.write_bytes(PNG_BYTES + b"-approved")
    scheduler_path = store.root / ".scheduler" / "2026-08-20.json"
    scheduler_path.parent.mkdir(parents=True)
    scheduler_path.write_text(
        json.dumps(
            {
                "run_date": "2026-08-20",
                "manifest_version": 1,
                "manifest_created_at": "2026-08-20T00:14:00+08:00",
                "expected_groups": [{"group_id": 1, "group_name": "测试群"}],
                "generation_status": "partial",
                "generation_completed_at": "2026-08-20T00:20:00+08:00",
                "generation_results": [
                    {"group_name": "测试群", "status": "failed", "error_type": IMAGE_GENERATION_FAILED},
                    {"group_name": "其他群", "status": "ready_to_send"},
                ],
                "email_completed_at": "2026-08-20T00:30:00+08:00",
                "email_status": "sent",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = adopt_image(store, "测试群", "2026-08-20", source)
    run = store.load_run("测试群", "2026-08-20")
    scheduler = json.loads(scheduler_path.read_text(encoding="utf-8"))

    assert result["status"] == READY_TO_SEND
    assert run["imagegen_ms"] == 601137
    assert run["stage_timings"]["imagegen_ms"] == 601137
    assert run["image_recovery_status"] == "manually_adopted_explicit_source"
    assert run["image_recovery"]["relative_path"] == "execution-approved/final.png"
    assert run["image_recovery"]["preserved_imagegen_ms"] is True
    assert len(run["image_recovery"]["sha256"]) == 64
    assert scheduler["generation_status"] == "success"
    assert scheduler["email_completed_at"] == "2026-08-20T00:30:00+08:00"
    assert scheduler["generation_results"][0]["status"] == "ready_to_send"
