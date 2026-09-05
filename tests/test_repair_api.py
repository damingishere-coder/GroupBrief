from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2_repair import router
from app.config.settings import Settings, get_settings
from app.repair.store import RepairIncidentStore


def test_repair_api_is_read_only_and_does_not_expose_error_body(tmp_path):
    settings = Settings(
        _env_file=None,
        output_root_override=str(tmp_path / "output"),
        repair_enabled=True,
    )
    incident = RepairIncidentStore(settings).record(
        scope="ranking",
        error_type="RANKING_FAILED",
        stage="render",
        source_path="daily/2026-09-05/group-opaque/run.json",
        error_summary="authorization=secret-value",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v2")
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    listing = client.get("/api/v2/repair/incidents")
    detail = client.get(f"/api/v2/repair/incidents/{incident['incident_id']}")
    missing = client.get("/api/v2/repair/incidents/00000000000000000000000000000000")

    assert listing.status_code == 200
    assert listing.json()["summary"]["queued"] == 1
    assert detail.status_code == 200
    assert detail.json()["fingerprint"] == incident["fingerprint"]
    assert "redacted_error_summary" not in detail.json()
    assert "secret-value" not in detail.text
    assert missing.status_code == 404
