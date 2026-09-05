"""自动维修只读 API。"""

from fastapi import APIRouter, Depends, HTTPException

from app.config.settings import Settings, get_settings
from app.repair.store import RepairIncidentStore, public_incident

router = APIRouter(prefix="/repair", tags=["v2-repair"])


@router.get("/incidents")
def list_repair_incidents(settings: Settings = Depends(get_settings)):
    store = RepairIncidentStore(settings)
    return {
        "schema_version": 1,
        "summary": store.summary(),
        "items": [public_incident(item) for item in store.list_incidents()],
    }


@router.get("/incidents/{incident_id}")
def repair_incident_detail(
    incident_id: str,
    settings: Settings = Depends(get_settings),
):
    value = RepairIncidentStore(settings).get(incident_id)
    if not value:
        raise HTTPException(status_code=404, detail="维修事件不存在")
    return public_incident(value)
