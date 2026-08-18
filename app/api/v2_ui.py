"""V2 管理 API（P8 前端依赖）。

Dashboard / 历史日报 / 系统健康 / Pipeline 手动操作 / 输出文件读取。
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.scheduler.period import PeriodResolver
from app.v2.constants import FILE_IMAGE
from app.v2.run_store import RunStore

router = APIRouter(prefix="/api/v2", tags=["v2-ui"])

# 允许前端直接读取的输出文件
ALLOWED_FILES = frozenset(
    {"messages.json", "ranking.json", "ranking.txt", "image_prompt.txt", "daily_image.png", "run.json"}
)


class PipelineGenerateBody(BaseModel):
    group_id: int | None = None
    run_date: str | None = None
    force: bool = False


class PipelineSendBody(BaseModel):
    group_id: int
    run_date: str | None = None


def _tz(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.app_timezone)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _store(settings: Settings) -> RunStore:
    return RunStore(settings.output_dir)


# ---------- Dashboard ----------


@router.get("/dashboard")
def dashboard(session: Session = Depends(repo.get_session), settings: Settings = Depends(get_settings)):
    tz = _tz(settings)
    now = datetime.now(tz)
    today = now.date().isoformat()

    resolver = PeriodResolver()
    window = resolver.resolve(run_date=now.date(), timezone=settings.app_timezone)
    store = _store(settings)
    groups = repo.list_groups(session, only_enabled=True)

    cards: list[dict] = []
    counts = {"pending": 0, "generated": 0, "sent": 0, "failed": 0}
    for g in groups:
        name = g.display_name or g.wechat_group_name
        run = store.load_run(name, today)
        status = run.get("status", "PENDING")
        image_path = store.image_path(name, today)
        image_url = ""
        if image_path.exists() and Path(image_path).stat().st_size > 0:
            from urllib.parse import quote

            image_url = f"/api/v2/files/{quote(name)}/{today}/{FILE_IMAGE}"
        cards.append(
            {
                "group_id": g.id,
                "group_name": name,
                "send_time": g.send_time,
                "schedule_rule": g.schedule_rule,
                "image_enabled": bool(g.image_enabled),
                "ranking_template": g.ranking_template,
                "image_prompt_template": g.image_prompt_template,
                "status": status,
                "period_start": run.get("period_start", ""),
                "period_end": run.get("period_end", ""),
                "message_count": run.get("message_count", 0),
                "speaker_count": run.get("speaker_count", 0),
                "image_url": image_url,
                "error": run.get("error") or run.get("image_error") or run.get("error_type") or "",
                "sent_at": run.get("sent_at", ""),
                "updated_at": run.get("updated_at", ""),
            }
        )
        if status in ("SENT",):
            counts["sent"] += 1
        elif status in ("IMAGE_READY", "READY_TO_SEND"):
            counts["generated"] += 1
        elif status == "FAILED":
            counts["failed"] += 1
        else:
            counts["pending"] += 1

    # 下一次发送时间（最早到点且未发送的群）
    next_send = ""
    upcoming = []
    for card in cards:
        if card["status"] not in ("IMAGE_READY", "READY_TO_SEND"):
            continue
        if card["sent_at"]:
            continue
        try:
            h, m = card["send_time"].split(":")
            send_at = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        except Exception:
            send_at = now.replace(hour=8, minute=30, second=0, microsecond=0)
        upcoming.append((send_at, card["group_name"]))
    if upcoming:
        earliest, name = min(upcoming, key=lambda x: x[0])
        next_send = f"{earliest.strftime('%H:%M')}（{name}）"

    return {
        "today": today,
        "should_run": window.should_run,
        "period_start": window.period_start_str(),
        "period_end": window.period_end_str(),
        "enabled_groups": len(cards),
        "counts": counts,
        "next_send": next_send,
        "cards": cards,
    }


# ---------- 历史日报 ----------


@router.get("/runs")
def list_runs(settings: Settings = Depends(get_settings), run_date: str | None = None):
    runs = _store(settings).list_runs(run_date)
    return {"runs": runs, "total": len(runs)}


@router.get("/runs/{group}/{run_date}")
def run_detail(group: str, run_date: str, settings: Settings = Depends(get_settings)):
    store = _store(settings)
    run = store.load_run(group, run_date)
    group_dir = store.group_dir(group, run_date)
    files = [p.name for p in group_dir.glob("*") if p.is_file()] if group_dir.exists() else []
    return {"run": run, "files": files}


# ---------- 系统健康 ----------


@router.get("/system/health")
def system_health(settings: Settings = Depends(get_settings)):
    checks: dict[str, dict] = {}

    from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource

    source = WeChatDataAnalysisSource(settings=settings)
    h = source.health_check()
    checks["wechat_data_analysis"] = {"ok": h.ok, "status": h.status.value, "detail": h.detail}

    checks["deepseek"] = {
        "ok": bool(settings.ai_api_key),
        "status": "OK" if settings.ai_api_key else "UNAVAILABLE",
        "detail": f"模型 {settings.ai_model}，API Key {'已配置' if settings.ai_api_key else '未配置'}",
    }

    from app.image.codex_generator import CodexImageGenerator

    codex_ok, codex_detail = CodexImageGenerator(settings=settings).health_check()
    checks["codex_imagegen"] = {"ok": codex_ok, "status": "OK" if codex_ok else "UNAVAILABLE", "detail": codex_detail}

    from app.sender.wechat_automation import WechatAutomationSender

    send_ok, send_detail = WechatAutomationSender(settings=settings).health_check()
    checks["wechat_sender"] = {"ok": send_ok, "status": "OK" if send_ok else "UNAVAILABLE", "detail": send_detail}

    # output 可写
    try:
        settings.ensure_dirs()
        test = settings.output_dir / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink()
        checks["output"] = {"ok": True, "status": "OK", "detail": "output 目录可写"}
    except Exception as e:
        checks["output"] = {"ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]}

    # 模板完整性
    from app.ranking.template_service import RankingTemplateService

    try:
        ranking_names = RankingTemplateService().list_templates()
        checks["templates"] = {"ok": True, "status": "OK", "detail": f"排行榜模板 {ranking_names}"}
    except Exception as e:
        checks["templates"] = {"ok": False, "status": "UNAVAILABLE", "detail": str(e)[:200]}

    return {"checks": checks}


# ---------- Pipeline 手动操作 ----------


@router.post("/pipeline/generate")
def pipeline_generate(body: PipelineGenerateBody):
    from app.pipeline.daily_pipeline import DailyPipeline

    pipeline = DailyPipeline(dry_run=False)
    if body.group_id:
        result = pipeline.force_generate(body.group_id, body.run_date)
        return {"results": [result]}
    results = pipeline.generate_all(run_date=body.run_date, force=body.force)
    return {"results": results}


@router.post("/pipeline/send-due")
def pipeline_send_due():
    from app.pipeline.daily_pipeline import DailyPipeline

    pipeline = DailyPipeline(dry_run=False)
    results = pipeline.send_due()
    return {"results": results}


@router.post("/pipeline/send")
def pipeline_send(body: PipelineSendBody):
    from app.pipeline.daily_pipeline import DailyPipeline

    pipeline = DailyPipeline(dry_run=False)
    result = pipeline.force_send(body.group_id, body.run_date)
    return {"result": result}


# ---------- 输出文件读取 ----------


@router.get("/files/{group}/{run_date}/{file_name}")
def read_output_file(
    group: str,
    run_date: str,
    file_name: str,
    settings: Settings = Depends(get_settings),
):
    if file_name not in ALLOWED_FILES:
        raise HTTPException(400, f"不允许访问的文件：{file_name}")
    path = _store(settings).group_dir(group, run_date) / file_name
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=file_name)
