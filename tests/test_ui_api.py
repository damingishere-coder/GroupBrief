"""V1 完善测试：统计 API、run 详情、Provider 状态入库。"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.db import repository as repo
from app.db.models import Group, ProviderHealth

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


def test_provider_health_refresh_is_explicit_and_get_is_passive():
    with client:
        with Session(repo.engine) as session:
            before_count = len(session.exec(select(ProviderHealth)).all())
        before = client.get("/api/system/providers")
        assert before.status_code == 200
        with Session(repo.engine) as session:
            assert len(session.exec(select(ProviderHealth)).all()) == before_count

        refreshed = client.post("/api/system/providers/refresh")
        assert refreshed.status_code == 200
        data = refreshed.json()
        assert "wechat_data_analysis" in data
        assert "mock" not in data

        cached = client.get("/api/system/providers")
        assert cached.status_code == 200
        assert cached.json()["wechat_data_analysis"]["checked_at"]


def test_resolve_api():
    with client:
        resp = client.get("/api/groups/resolve", params={"name": "产品"})
        # 未配置真实 Provider 时可能返回空列表，但接口必须可用
        assert resp.status_code == 200


def test_v2_image_theme_catalog_shape_and_order():
    with client:
        response = client.get("/api/v2/image-themes")
        assert response.status_code == 200
        themes = response.json()["themes"]
        assert [item["key"] for item in themes[:3]] == ["ai_free", "random_preset", "custom"]
        assert len(themes) == 25
        assert sum(item["kind"] == "preset" for item in themes) == 22
        assert themes[3]["key"] == "silkscreen_editorial"
        assert themes[-1]["key"] == "mineral_pigment"
        assert all(
            set(item)
            == {"key", "label", "description", "kind", "category", "swatches", "variation_count", "preview_url"}
            for item in themes
        )
        assert themes[0]["preview_url"] == ""
        assert themes[3]["preview_url"] == "/assets/image-theme-previews/silkscreen_editorial.webp"


@pytest.mark.parametrize("bad_date", ["2026-02-30", "2026-8-18", "not-a-date"])
def test_v2_invalid_dates_return_400(bad_date):
    with client:
        assert client.get("/api/v2/runs", params={"run_date": bad_date}).status_code == 400
        assert client.get(f"/api/v2/runs/test-group/{bad_date}").status_code == 400
        assert client.get(f"/api/v2/files/test-group/{bad_date}/ranking.txt").status_code == 400


def test_group_create_rejects_unsafe_output_names():
    with client:
        for bad_name in ("..", "../logs", r"..\logs", r"C:\Windows", r"\\server\share"):
            response = client.post("/api/groups", json={"display_name": bad_name})
            assert response.status_code == 422


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
            assert listed["schedule_rule"] == "daily_previous_day"
            assert listed["image_theme"] == "custom"
            assert listed["image_theme_custom"] == "手账拼贴"

            # 切换到公开预设时保存稳定键并清空旧自定义文本。
            updated = client.put(f"/api/groups/{group_id}", json={"image_theme": "ink_wash_editorial"})
            assert updated.status_code == 200
            listed = next(item for item in client.get("/api/groups").json() if item["id"] == group_id)
            assert listed["image_theme"] == "ink_wash_editorial"
            assert listed["image_theme_custom"] == ""
            prompt_config = client.get(f"/api/groups/{group_id}/image-prompt").json()
            assert prompt_config["image_theme"] == "ink_wash_editorial"
            assert prompt_config["resolved_theme"]["resolved_theme"] == "ink_wash_editorial"

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
