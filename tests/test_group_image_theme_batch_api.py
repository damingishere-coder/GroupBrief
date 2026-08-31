from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import groups as groups_api
from app.db import repository as repo
from app.db.models import Group, Run


@pytest.fixture
def batch_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    api = FastAPI()
    api.include_router(groups_api.router)
    api.dependency_overrides[repo.get_session] = override_session
    with TestClient(api) as client:
        yield client, engine


def save_group(engine, name: str, **values) -> int:
    with Session(engine) as session:
        group = repo.save_group(
            session,
            Group(
                display_name=name,
                image_theme="ai_free",
                image_theme_custom="",
                image_prompt_override=f"{name} 的专属 Prompt",
                send_time="08:30",
                send_target=f"{name}发送目标",
                wechat_send_enabled=True,
                **values,
            ),
        )
        return int(group.id)


def snapshot_group(engine, group_id: int) -> dict:
    with Session(engine) as session:
        group = session.get(Group, group_id)
        return {
            field: getattr(group, field)
            for field in Group.model_fields
            if field != "updated_at"
        } | {"updated_at": group.updated_at}


def test_batch_image_theme_updates_multiple_groups_and_only_theme_fields(batch_client, tmp_path):
    client, engine = batch_client
    first_id = save_group(engine, "一群")
    disabled_id = save_group(engine, "停用群", enabled=False)
    before = {group_id: snapshot_group(engine, group_id) for group_id in (first_id, disabled_id)}
    manifest = tmp_path / "existing-run.json"
    manifest.write_text('{"status":"generated","prompt":"历史 Prompt"}', encoding="utf-8")
    manifest_before = manifest.read_bytes()
    with Session(engine) as session:
        history = Run(report_date="2026-08-30", status="success")
        session.add(history)
        session.commit()
        history_id = history.id

    response = client.put(
        "/api/groups/batch/image-theme",
        json={
            "group_ids": [first_id, disabled_id],
            "image_theme": "custom",
            "image_theme_custom": "  低饱和黏土摄影  ",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "requested_count": 2,
        "success": [
            {"group_id": first_id, "group_name": "一群"},
            {"group_id": disabled_id, "group_name": "停用群"},
        ],
        "failed": [],
    }
    for group_id in (first_id, disabled_id):
        after = snapshot_group(engine, group_id)
        assert after["image_theme"] == "custom"
        assert after["image_theme_custom"] == "低饱和黏土摄影"
        assert after["updated_at"] >= before[group_id]["updated_at"]
        unchanged = set(before[group_id]) - {"image_theme", "image_theme_custom", "updated_at"}
        assert {field: after[field] for field in unchanged} == {
            field: before[group_id][field] for field in unchanged
        }
    with Session(engine) as session:
        assert session.get(Run, history_id).status == "success"
    assert manifest.read_bytes() == manifest_before


def test_batch_image_theme_reports_missing_and_deleted_without_rolling_back_success(batch_client):
    client, engine = batch_client
    active_id = save_group(engine, "正常群")
    deleted_id = save_group(engine, "回收站群")
    with Session(engine) as session:
        deleted = session.get(Group, deleted_id)
        deleted.deleted_at = datetime.now()
        session.add(deleted)
        session.commit()
    missing_id = 999_999

    response = client.put(
        "/api/groups/batch/image-theme",
        json={
            "group_ids": [active_id, missing_id, deleted_id],
            "image_theme": "random_preset",
            "image_theme_custom": "应被清空",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "partial",
        "requested_count": 3,
        "success": [{"group_id": active_id, "group_name": "正常群"}],
        "failed": [
            {"group_id": missing_id, "code": "GROUP_NOT_FOUND", "reason": "群不存在"},
            {"group_id": deleted_id, "code": "GROUP_DELETED", "reason": "群已移入回收站"},
        ],
    }
    assert snapshot_group(engine, active_id)["image_theme"] == "random_preset"
    assert snapshot_group(engine, active_id)["image_theme_custom"] == ""
    assert snapshot_group(engine, deleted_id)["image_theme"] == "ai_free"


def test_batch_image_theme_returns_failed_when_every_target_is_invalid(batch_client):
    client, engine = batch_client
    deleted_id = save_group(engine, "已删除群")
    with Session(engine) as session:
        deleted = session.get(Group, deleted_id)
        deleted.deleted_at = datetime.now()
        session.add(deleted)
        session.commit()

    response = client.put(
        "/api/groups/batch/image-theme",
        json={
            "group_ids": [999_998, deleted_id],
            "image_theme": "ai_free",
            "image_theme_custom": "",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "failed",
        "requested_count": 2,
        "success": [],
        "failed": [
            {"group_id": 999_998, "code": "GROUP_NOT_FOUND", "reason": "群不存在"},
            {"group_id": deleted_id, "code": "GROUP_DELETED", "reason": "群已移入回收站"},
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"group_ids": [], "image_theme": "ai_free"},
        {"group_ids": [1, 1], "image_theme": "ai_free"},
        {"group_ids": [0], "image_theme": "ai_free"},
        {"group_ids": [1], "image_theme": "not_a_theme"},
        {"group_ids": [1], "image_theme": "custom", "image_theme_custom": ""},
        {"group_ids": [1], "image_theme": "custom", "image_theme_custom": "a" * 81},
        {"group_ids": [1], "image_theme": "custom", "image_theme_custom": "多行\n主题"},
    ],
)
def test_batch_image_theme_global_validation_is_422_with_zero_writes(batch_client, payload):
    client, engine = batch_client
    group_id = save_group(engine, "校验群")
    payload["group_ids"] = [group_id] if payload.get("group_ids") == [1] else payload["group_ids"]
    before = snapshot_group(engine, group_id)

    response = client.put("/api/groups/batch/image-theme", json=payload)

    assert response.status_code == 422
    assert snapshot_group(engine, group_id) == before


def test_batch_image_theme_continues_after_database_failure(batch_client, monkeypatch):
    client, engine = batch_client
    first_id = save_group(engine, "成功一群")
    failed_id = save_group(engine, "失败群")
    last_id = save_group(engine, "成功二群")
    original = repo.update_group_image_theme

    def fail_middle(session, group_id, **values):
        if group_id == failed_id:
            raise OperationalError("UPDATE groups", {}, RuntimeError("simulated"))
        return original(session, group_id, **values)

    monkeypatch.setattr(repo, "update_group_image_theme", fail_middle)
    response = client.put(
        "/api/groups/batch/image-theme",
        json={
            "group_ids": [first_id, failed_id, last_id],
            "image_theme": "ink_wash_editorial",
            "image_theme_custom": "",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "partial"
    assert [item["group_id"] for item in data["success"]] == [first_id, last_id]
    assert data["failed"] == [{
        "group_id": failed_id,
        "code": "DATABASE_SAVE_FAILED",
        "reason": "数据库保存失败，请重试",
    }]
    assert snapshot_group(engine, first_id)["image_theme"] == "ink_wash_editorial"
    assert snapshot_group(engine, failed_id)["image_theme"] == "ai_free"
    assert snapshot_group(engine, last_id)["image_theme"] == "ink_wash_editorial"
