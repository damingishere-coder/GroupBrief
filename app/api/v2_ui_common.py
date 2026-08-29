"""V2 管理 API 的共享请求模型与路径边界。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.config.settings import Settings
from app.v2.run_store import RunStore, validate_run_date


ALLOWED_FILES = frozenset(
    {
        "messages.json",
        "ranking.json",
        "ranking.txt",
        "image_prompt.txt",
        "image_prompt.original.txt",
        "daily_image.png",
        "daily_image.previous.png",
        "run.json",
    }
)


class PipelineGenerateBody(BaseModel):
    group_id: int | None = None
    run_date: str | None = None
    force: bool = False
    refresh_messages: bool = False


class PipelineSendBody(BaseModel):
    group_id: int
    run_date: str | None = None
    confirm_regenerated: bool = False
    confirm_late_send: bool = False


class ResolveSendUnknownBody(BaseModel):
    group_id: int
    run_date: str
    resolution: Literal["text_sent", "not_sent"]
    expected_send_unknown_at: str = Field(min_length=10, max_length=80)


class ResolvePromptUnknownBody(BaseModel):
    group_id: int
    run_date: str
    expected_operation_id: str = Field(min_length=8, max_length=80)


class ResetSendFailureBody(BaseModel):
    group_id: int
    run_date: str
    expected_updated_at: str = Field(min_length=10, max_length=80)
    expected_state_version: int = Field(ge=1)


class ResolveManualSendBody(BaseModel):
    group_id: int
    run_date: str
    resolution: Literal["all_sent", "text_sent", "not_sent"]
    expected_updated_at: str = Field(min_length=10, max_length=80)


class ImageThemeResolveBody(BaseModel):
    image_theme: str
    image_theme_custom: str = ""
    prompt: str = ""
    group_id: int | str | None = None
    run_date: str = ""


class RunPromptUpdateBody(BaseModel):
    content: str
    expected_revision: str
    image_theme: str
    image_theme_custom: str = ""


class StableRunTarget(BaseModel):
    group_id: int
    wechat_group_id: str
    run_date: str


class BatchRunTargetsBody(BaseModel):
    targets: list[StableRunTarget] = Field(min_length=1, max_length=50)


class CandidateClaimBody(BaseModel):
    job_id: str = Field(min_length=8, max_length=64)
    candidate_id: str = Field(min_length=64, max_length=64)


class RetryBody(BaseModel):
    group_id: int | None = None
    run_date: str | None = None


def timezone_for(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def make_store(settings: Settings) -> RunStore:
    return RunStore(settings.output_dir)


def safe_group_dir(store: RunStore, group: str, run_date: str) -> Path:
    """把 RunStore 的路径拒绝统一转换成明确的客户端错误。"""
    try:
        return store.group_dir(group, run_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def validate_api_run_date(run_date: str) -> str:
    try:
        return validate_run_date(run_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
