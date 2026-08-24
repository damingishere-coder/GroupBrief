from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import groups as groups_api
from app.api import v2_ui
from app.config.settings import get_settings
from app.db import repository as repo
from app.db.models import Group, GroupRun, Report, Run
from app.v2.run_store import RunStore


@pytest.fixture
def archive_client(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    output_dir = tmp_path / "output"

    def override_session():
        with Session(engine) as session:
            yield session

    api = FastAPI()
    api.include_router(groups_api.router)
    api.include_router(v2_ui.router)
    api.dependency_overrides[repo.get_session] = override_session
    api.dependency_overrides[get_settings] = lambda: SimpleNamespace(output_dir=output_dir)

    with TestClient(api) as client:
        yield client, engine, output_dir


def _save_group(engine, **values) -> Group:
    with Session(engine) as session:
        group = repo.save_group(session, Group(**values))
        session.expunge(group)
        return group


def _save_v2_run(store: RunStore, group: Group | None, name: str, date: str, **fields) -> dict:
    data = {
        "group_id": str(group.id) if group is not None else str(fields.pop("group_id", "9999")),
        "wechat_group_id": group.wechat_group_id if group is not None else fields.pop("wechat_group_id", "orphan@chatroom"),
        "group_name": name,
        "run_date": date,
        "status": "READY_TO_SEND",
        "period_start": f"{date} 00:00:00",
        "period_end": f"{date} 23:59:59",
        "message_count": 10,
        "speaker_count": 3,
        **fields,
    }
    return store.save_run(name, date, data)


def test_archive_catalog_includes_empty_groups_groups_renamed_runs_and_orphans(archive_client):
    client, engine, output_dir = archive_client
    renamed = _save_group(
        engine,
        display_name="新群名",
        wechat_group_id="renamed@chatroom",
        wechat_group_name="新群名",
    )
    empty = _save_group(engine, display_name="尚无归档群", wechat_group_id="empty@chatroom")
    same_name_a = _save_group(engine, display_name="同名群", wechat_group_id="same-a@chatroom")
    same_name_b = _save_group(engine, display_name="同名群", wechat_group_id="same-b@chatroom")
    deleted = _save_group(engine, display_name="已删除群", wechat_group_id="deleted@chatroom")

    store = RunStore(output_dir)
    _save_v2_run(store, renamed, "旧群名", "2026-08-18")
    _save_v2_run(store, same_name_a, "同名群", "2026-08-19")
    _save_v2_run(store, deleted, "已删除群", "2026-08-20")
    _save_v2_run(
        store,
        None,
        "历史遗留群",
        "2026-08-17",
        group_id="8888",
        wechat_group_id="missing@chatroom",
    )
    with Session(engine) as session:
        repo.delete_group(session, deleted.id)

    # V1 日期根目录只有 handoff，不得被识别成 V2 群归档。
    v1_dir = output_dir / "2026-08-18" / "旧V1群"
    v1_dir.mkdir(parents=True)
    (v1_dir / "handoff.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

    response = client.get("/api/v2/archive/groups")
    assert response.status_code == 200
    body = response.json()
    by_key = {item["archive_key"]: item for item in body["groups"]}

    assert by_key[f"group:{empty.id}"]["runs"] == []
    assert by_key[f"group:{renamed.id}"]["display_name"] == "新群名"
    assert by_key[f"group:{renamed.id}"]["runs"][0]["group_name"] == "旧群名"
    assert by_key[f"group:{same_name_a.id}"]["run_count"] == 1
    assert by_key[f"group:{same_name_b.id}"]["run_count"] == 0
    assert by_key[f"group:{deleted.id}"]["state"] == "deleted"
    assert by_key[f"group:{deleted.id}"]["run_dates"] == ["2026-08-20"]
    orphan = next(item for item in body["groups"] if item["state"] == "orphaned")
    assert orphan["display_name"] == "历史遗留群"
    assert all(item["display_name"] != "旧V1群" for item in body["groups"])
    assert body["active_count"] == 4
    assert body["trash_count"] == 2

    # 旧 runs 接口保持原结构，避免影响其他页面。
    legacy_compatible = client.get("/api/v2/runs")
    assert legacy_compatible.status_code == 200
    assert {"runs", "total"} <= set(legacy_compatible.json())


def test_archive_group_name_is_display_only_not_an_ownership_key(archive_client):
    client, engine, output_dir = archive_client
    group = _save_group(
        engine,
        display_name="仅同名群",
        wechat_group_id="stable-same-name@chatroom",
    )
    store = RunStore(output_dir)
    _save_v2_run(
        store,
        None,
        "仅同名群",
        "2026-08-20",
        group_id="",
        wechat_group_id="",
    )

    response = client.get("/api/v2/archive/groups")
    assert response.status_code == 200
    groups = response.json()["groups"]
    current = next(item for item in groups if item["group_id"] == group.id)
    orphan = next(
        item
        for item in groups
        if item["state"] == "orphaned" and item["display_name"] == "仅同名群"
    )
    assert current["run_count"] == 0
    assert orphan["run_count"] == 1


def test_run_detail_only_lists_downloadable_files_and_serves_original_png(archive_client):
    client, _, output_dir = archive_client
    group_name = "文件预览群"
    run_date = "2026-08-22"
    store = RunStore(output_dir)
    _save_v2_run(store, None, group_name, run_date)
    group_dir = store.group_dir(group_name, run_date)
    image_bytes = b"\x89PNG\r\n\x1a\noriginal-image"
    (group_dir / "daily_image.png").write_bytes(image_bytes)
    (group_dir / "daily_image.previous.png").write_bytes(b"\x89PNG\r\n\x1a\nprevious-image")
    (group_dir / "微信群日报-测试.png").write_bytes(b"\x89PNG\r\n\x1a\nextra-image")
    (group_dir / "private.txt").write_text("not public", encoding="utf-8")

    detail = client.get(f"/api/v2/runs/{group_name}/{run_date}")
    assert detail.status_code == 200
    assert detail.json()["files"] == [
        "daily_image.png",
        "daily_image.previous.png",
        "run.json",
    ]

    image = client.get(f"/api/v2/files/{group_name}/{run_date}/daily_image.png")
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert "daily_image.png" in image.headers["content-disposition"]
    assert image.content == image_bytes

    assert client.get(f"/api/v2/files/{group_name}/{run_date}/微信群日报-测试.png").status_code == 400
    assert client.get(f"/api/v2/files/{group_name}/{run_date}/..%2Fprivate.txt").status_code != 200


def test_v2_file_and_run_routes_reject_unsafe_group_paths(archive_client):
    client, _, _ = archive_client
    run_date = "2026-08-24"

    for encoded_group in ("..%5Clogs", "C:%5CWindows", "%5C%5Cserver%5Cshare"):
        assert client.get(f"/api/v2/runs/{encoded_group}/{run_date}").status_code == 400
        assert client.get(f"/api/v2/files/{encoded_group}/{run_date}/ranking.txt").status_code == 400
        assert (
            client.put(
                f"/api/v2/runs/{encoded_group}/{run_date}/prompt",
                json={
                    "content": "安全 Prompt",
                    "expected_revision": "missing",
                    "image_theme": "random_preset",
                },
            ).status_code
            == 400
        )

    response = client.post(
        "/api/v2/image-themes/resolve",
        json={
            "image_theme": "random_preset",
            "group_id": r"..\logs",
            "run_date": "2026-08-24",
        },
    )
    assert response.status_code == 400


def test_soft_delete_preserves_database_history_and_output_then_restores_disabled(archive_client):
    client, engine, output_dir = archive_client
    group = _save_group(
        engine,
        display_name="软删除测试群",
        wechat_group_id="soft-delete@chatroom",
        enabled=True,
        wechat_send_enabled=True,
    )
    store = RunStore(output_dir)
    _save_v2_run(store, group, group.display_name, "2026-08-21")
    ranking_path = store.ranking_txt_path(group.display_name, "2026-08-21")
    ranking_path.write_text("历史排行榜", encoding="utf-8")

    with Session(engine) as session:
        run = Run(report_date="2026-08-21", status="success")
        session.add(run)
        session.commit()
        session.refresh(run)
        group_run = GroupRun(run_id=run.id, group_id=group.id, ranking_status="success", prompt_status="success")
        session.add(group_run)
        session.commit()
        session.refresh(group_run)
        session.add(Report(group_run_id=group_run.id, ranking_text="历史排行榜"))
        session.commit()

    deleted = client.delete(f"/api/groups/{group.id}")
    repeated = client.delete(f"/api/groups/{group.id}")
    assert deleted.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["deleted_at"] == deleted.json()["deleted_at"]
    assert ranking_path.read_text(encoding="utf-8") == "历史排行榜"
    assert all(item["id"] != group.id for item in client.get("/api/groups").json())

    with Session(engine) as session:
        stored = session.get(Group, group.id)
        assert stored is not None
        assert stored.deleted_at is not None
        assert stored.enabled is False
        assert stored.wechat_send_enabled is False
        assert session.exec(select(GroupRun).where(GroupRun.group_id == group.id)).all()
        assert session.exec(select(Report)).all()

    archived = client.get("/api/v2/archive/groups").json()["groups"]
    recycled = next(item for item in archived if item["group_id"] == group.id)
    assert recycled["state"] == "deleted"
    assert recycled["run_count"] == 1

    restored = client.post(f"/api/groups/{group.id}/restore")
    assert restored.status_code == 200
    assert restored.json()["enabled"] is False
    listed = next(item for item in client.get("/api/groups").json() if item["id"] == group.id)
    assert listed["enabled"] is False
    assert listed["wechat_send_enabled"] is False
    assert ranking_path.exists()


def test_soft_delete_missing_group_returns_404(archive_client):
    client, _, _ = archive_client
    assert client.delete("/api/groups/999999").status_code == 404
    assert client.post("/api/groups/999999/restore").status_code == 404
