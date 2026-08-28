"""V2 历史恢复预览与显式确认 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.config.settings import Settings, get_settings
from app.scheduler.recovery_planner import (
    RecoveryPlanChangedError,
    RecoveryPlanner,
    RecoverySelectionError,
)
from app.services.generation_runtime import GenerationBusyError

router = APIRouter(prefix="/recovery", tags=["v2-recovery"])


class RecoverySelection(BaseModel):
    run_date: str
    group_id: int = Field(gt=0)


class RecoveryConfirmBody(BaseModel):
    expected_version: str = Field(min_length=64, max_length=64)
    tasks: list[RecoverySelection] = Field(min_length=1, max_length=180)


class EmptyManifestRepairBody(BaseModel):
    run_date: str
    expected_state_version: int = Field(ge=1)
    expected_group_ids: list[int] = Field(min_length=1, max_length=100)


@router.get("/backlog")
def recovery_backlog(
    lookback_days: int = Query(default=30, ge=3, le=30),
    settings: Settings = Depends(get_settings),
):
    return RecoveryPlanner(settings).preview(lookback_days=lookback_days)


@router.post("/confirm")
def confirm_recovery(
    body: RecoveryConfirmBody,
    settings: Settings = Depends(get_settings),
):
    try:
        return RecoveryPlanner(settings).confirm_generation(
            [item.model_dump() for item in body.tasks],
            expected_version=body.expected_version,
        )
    except RecoveryPlanChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecoverySelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/repair-empty-manifest")
def repair_empty_manifest(
    body: EmptyManifestRepairBody,
    settings: Settings = Depends(get_settings),
):
    try:
        return RecoveryPlanner(settings).repair_empty_manifest_and_generate(
            body.run_date,
            expected_state_version=body.expected_state_version,
            expected_group_ids=body.expected_group_ids,
        )
    except RecoveryPlanChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RecoverySelectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
