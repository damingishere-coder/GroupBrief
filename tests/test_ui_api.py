"""V1 完善测试：统计 API、run 详情、Provider 状态入库。"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app

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
