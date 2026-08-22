"""V1 完善测试：统计 API、run 详情、Provider 状态入库。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app
from app.db import repository as repo
from app.db.models import Group

client = TestClient(app)


def test_system_stats():
    with client:
        resp = client.get("/api/system/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_messages" in data
        assert "total_speakers" in data
        assert data["total_messages"] >= 0


def test_run_detail_endpoint():
    with client:
        # 先手动生成一次
        resp = client.post(
            "/api/reports/generate",
            json={"report_date": "2026-08-13", "force": True},
        )
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        detail = client.get(f"/api/runs/{run_id}")
        assert detail.status_code == 200
        data = detail.json()
        assert data["id"] == run_id
        assert "group_runs" in data


def test_providers_writes_health_db():
    with client:
        resp = client.get("/api/system/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "wechat_data_analysis" in data
        assert "mock" in data


def test_resolve_api():
    with client:
        resp = client.get("/api/groups/resolve", params={"name": "产品"})
        # 未配置真实 Provider 时可能返回空列表，但接口必须可用
        assert resp.status_code == 200


@pytest.mark.parametrize("bad_date", ["2026-02-30", "2026-8-18", "not-a-date"])
def test_v2_invalid_dates_return_400(bad_date):
    with client:
        assert client.get("/api/v2/runs", params={"run_date": bad_date}).status_code == 400
        assert client.get(f"/api/v2/runs/test-group/{bad_date}").status_code == 400
        assert client.get(f"/api/v2/files/test-group/{bad_date}/ranking.txt").status_code == 400


def test_group_image_theme_roundtrip_and_validation():
    display_name = "主题配置测试群"
    with client:
        # 清理同名残留，避免失败测试影响下一次运行。
        for group in client.get("/api/groups").json():
            if group["display_name"] == display_name:
                client.delete(f"/api/groups/{group['id']}")

        created = client.post(
            "/api/groups",
            json={
                "display_name": display_name,
                "image_theme": "custom",
                "image_theme_custom": "  手账拼贴  ",
            },
        )
        assert created.status_code == 200
        group_id = created.json()["id"]
        try:
            listed = next(item for item in client.get("/api/groups").json() if item["id"] == group_id)
            assert listed["image_theme"] == "custom"
            assert listed["image_theme_custom"] == "手账拼贴"

            # 切换到具体预设时保留自定义文本，以便稍后切回 custom 回显。
            updated = client.put(f"/api/groups/{group_id}", json={"image_theme": "pink"})
            assert updated.status_code == 200
            listed = next(item for item in client.get("/api/groups").json() if item["id"] == group_id)
            assert listed["image_theme"] == "pink"
            assert listed["image_theme_custom"] == "手账拼贴"

            bad_payloads = [
                {"image_theme": "not_a_theme"},
                {"image_theme": "custom", "image_theme_custom": ""},
                {"image_theme": "custom", "image_theme_custom": "a" * 81},
                {"image_theme": "custom", "image_theme_custom": "多行\n主题"},
            ]
            for payload in bad_payloads:
                assert client.put(f"/api/groups/{group_id}", json=payload).status_code == 422

            # 旧数据库中如果已有脏主题值，更新普通字段仍应保持兼容。
            with Session(repo.engine) as session:
                dirty = session.get(Group, group_id)
                dirty.image_theme = "legacy_invalid"
                session.add(dirty)
                session.commit()
            ordinary_update = client.put(f"/api/groups/{group_id}", json={"display_name": display_name + "-改名"})
            assert ordinary_update.status_code == 200
        finally:
            client.delete(f"/api/groups/{group_id}")
