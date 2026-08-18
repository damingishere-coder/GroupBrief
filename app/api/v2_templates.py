"""V2 模板管理 API（P3：排行榜模板 CRUD；P4 扩展生图 Prompt 模板）。

前端模板中心依赖本接口：列表 / 读取 / 保存 / 预览 / 恢复默认 / 删除。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.ai.prompt_templates import (
    ImagePromptTemplateError,
    ImagePromptTemplateService,
)
from app.ranking.engine_types import RankingResult, TopSpeaker
from app.ranking.renderer import render_ranking
from app.ranking.template_service import TemplateError, RankingTemplateService

router = APIRouter(prefix="/api/v2/templates", tags=["v2-templates"])

_service = RankingTemplateService()
_prompt_service = ImagePromptTemplateService()


class TemplateContent(BaseModel):
    content: str


# ---------- 排行榜模板 ----------


@router.get("/ranking")
def list_ranking_templates():
    names = _service.list_templates()
    previews: dict[str, str] = {}
    for name in names:
        try:
            text = _service.read(name)
            previews[name] = render_ranking(_preview_result(), text)[:200]
        except TemplateError:
            previews[name] = "(模板有误，预览失败)"
    return {"templates": names, "previews": previews}


@router.get("/ranking/{name}")
def get_ranking_template(name: str):
    try:
        return {"name": name, "content": _service.read(name)}
    except TemplateError as e:
        raise HTTPException(404, str(e))


@router.put("/ranking/{name}")
def save_ranking_template(name: str, payload: TemplateContent):
    try:
        _service.save(name, payload.content)
        return {"ok": True, "name": name}
    except TemplateError as e:
        raise HTTPException(400, str(e))


@router.post("/ranking/{name}/reset")
def reset_ranking_template(name: str):
    try:
        content = _service.reset(name)
        return {"ok": True, "name": name, "content": content}
    except TemplateError as e:
        raise HTTPException(400, str(e))


@router.delete("/ranking/{name}")
def delete_ranking_template(name: str):
    try:
        _service.delete(name)
        return {"ok": True}
    except TemplateError as e:
        raise HTTPException(400, str(e))


@router.post("/ranking/{name}/preview")
def preview_ranking_template(name: str, payload: TemplateContent):
    """用示例数据渲染模板，供前端预览。"""
    try:
        return {"ok": True, "rendered": render_ranking(_preview_result(), payload.content)}
    except TemplateError as e:
        raise HTTPException(400, str(e))


# ---------- 生图 Prompt 模板（P4） ----------


@router.get("/image_prompt")
def list_image_prompt_templates():
    return {"templates": _prompt_service.list_templates()}


@router.get("/image_prompt/{name}")
def get_image_prompt_template(name: str):
    try:
        return {"name": name, "content": _prompt_service.read(name)}
    except ImagePromptTemplateError as e:
        raise HTTPException(404, str(e))


@router.put("/image_prompt/{name}")
def save_image_prompt_template(name: str, payload: TemplateContent):
    try:
        _prompt_service.save(name, payload.content)
        return {"ok": True, "name": name}
    except ImagePromptTemplateError as e:
        raise HTTPException(400, str(e))


@router.post("/image_prompt/{name}/reset")
def reset_image_prompt_template(name: str):
    try:
        content = _prompt_service.reset(name)
        return {"ok": True, "name": name, "content": content}
    except ImagePromptTemplateError as e:
        raise HTTPException(400, str(e))


@router.delete("/image_prompt/{name}")
def delete_image_prompt_template(name: str):
    try:
        _prompt_service.delete(name)
        return {"ok": True}
    except ImagePromptTemplateError as e:
        raise HTTPException(400, str(e))


def _preview_result() -> RankingResult:
    return RankingResult(
        group_name="茶馆V3.0（三周年纪念）🐮🐴",
        period_start="2026-08-17 00:00:00",
        period_end="2026-08-17 23:59:59",
        speaker_count=27,
        message_count=409,
        top_speakers=[
            TopSpeaker(rank=1, name="停用", count=94),
            TopSpeaker(rank=2, name="罗斯", count=78),
            TopSpeaker(rank=3, name="啊菌菌阿菌", count=53),
            TopSpeaker(rank=4, name="杯面大英雄", count=39),
            TopSpeaker(rank=5, name="一颗苹果", count=35),
        ],
    )
