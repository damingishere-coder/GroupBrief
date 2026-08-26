"""V2 管理 API 聚合入口（P8 前端依赖）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.v2_ui_common import (
    ALLOWED_FILES,
    ImageThemeResolveBody,
    PipelineGenerateBody,
    PipelineSendBody,
    ResolveManualSendBody,
    ResolveSendUnknownBody,
    RetryBody,
    RunPromptUpdateBody,
    make_store as _store,
    safe_group_dir as _safe_group_dir,
    timezone_for as _tz,
    validate_api_run_date as _validate_run_date,
)
from app.api.v2_ui_images import (
    get_run_prompt,
    image_themes,
    rebuild_run_prompt,
    refresh_run_messages,
    regenerate_run_image,
    resolve_theme_preview,
    restore_run_prompt,
    router as image_router,
    update_run_prompt,
)
from app.api.v2_ui_read import (
    archive_groups,
    dashboard,
    list_runs,
    read_output_file,
    router as read_router,
    run_detail,
)
from app.config.settings import Settings, get_settings
from app.v2.constants import RUN_STATE_CORRUPT


router = APIRouter(prefix="/api/v2", tags=["v2-ui"])
router.include_router(read_router)
router.include_router(image_router)


@router.get("/system/health")
def system_health(settings: Settings = Depends(get_settings)):
    checks: dict[str, dict] = {}

    from app.data_sources.wechat_data_analysis import WeChatDataAnalysisSource

    source = WeChatDataAnalysisSource(settings=settings)
    health = source.health_check()
    checks["wechat_data_analysis"] = {
        "ok": health.ok,
        "status": health.status.value,
        "detail": health.detail,
    }

    from app.providers.ai.codex import CodexGPTProvider

    codex_summary = CodexGPTProvider(settings)
    codex_summary_report = codex_summary.health_report()
    codex_summary_ok, codex_summary_detail = codex_summary.health_check(
        codex_summary_report
    )
    checks["codex_summary"] = {
        "ok": codex_summary_ok,
        "status": "OK" if codex_summary_ok else "UNAVAILABLE",
        "detail": codex_summary_detail,
        "model": codex_summary_report["model"],
        "binary": codex_summary_report["binary"],
        "version": codex_summary_report["version"],
    }

    checks["deepseek_fallback"] = {
        "ok": bool(settings.ai_api_key),
        "status": "OK" if settings.ai_api_key else "UNAVAILABLE",
        "detail": (
            f"备用模型 {settings.ai_model}，"
            f"API Key {'已配置' if settings.ai_api_key else '未配置'}"
        ),
    }

    from app.image.codex_generator import CodexImageGenerator

    codex = CodexImageGenerator(settings=settings)
    codex_report = codex.health_report()
    codex_ok, codex_detail = codex.health_check(codex_report)
    checks["codex_imagegen"] = {
        "ok": codex_ok,
        "status": "OK" if codex_ok else "UNAVAILABLE",
        "detail": codex_detail,
        "binary": codex_report["binary"],
        "version": codex_report["version"],
        "last_image_smoke": codex_report["last_image_smoke"],
    }

    from app.sender.wechat_native import WechatNativeSender, create_wechat_sender

    sender = create_wechat_sender(settings=settings)
    if isinstance(sender, WechatNativeSender):
        sender_report = sender.health_report()
        send_ok, send_detail = sender.health_check(sender_report)
    else:
        send_ok, send_detail = sender.health_check()
        sender_report = {"ok": send_ok}
    checks["wechat_sender"] = {
        "ok": send_ok,
        "status": "OK" if send_ok else "UNAVAILABLE",
        "detail": send_detail,
        "dependencies": sender_report.get(
            "dependencies",
            {"ok": send_ok, "detail": send_detail},
        ),
        "desktop": sender_report.get(
            "desktop",
            {"ok": send_ok, "detail": send_detail},
        ),
        "ocr": sender_report.get(
            "ocr",
            {"ok": send_ok, "detail": send_detail},
        ),
        "clipboard": sender_report.get(
            "clipboard",
            {"ok": send_ok, "detail": send_detail},
        ),
        "window": sender_report.get(
            "window",
            {"ok": send_ok, "detail": send_detail},
        ),
    }

    try:
        settings.ensure_dirs()
        test_path = settings.output_dir / ".write_test"
        test_path.write_text("ok", encoding="utf-8")
        test_path.unlink()
        checks["output"] = {
            "ok": True,
            "status": "OK",
            "detail": "output 目录可写",
        }
    except Exception as exc:
        checks["output"] = {
            "ok": False,
            "status": "UNAVAILABLE",
            "detail": str(exc)[:200],
        }

    from app.ranking.template_service import RankingTemplateService

    try:
        ranking_names = RankingTemplateService().list_templates()
        checks["templates"] = {
            "ok": True,
            "status": "OK",
            "detail": f"排行榜模板 {ranking_names}",
        }
    except Exception as exc:
        checks["templates"] = {
            "ok": False,
            "status": "UNAVAILABLE",
            "detail": str(exc)[:200],
        }

    store = _store(settings)
    runs = store.list_runs()
    last_run = runs[0] if runs else {}
    incomplete = sum(
        1 for run in runs if run.get("status") not in ("SENT", "FAILED")
    )
    checks["recent_task"] = {
        "ok": True,
        "status": "OK",
        "detail": (
            f"最近：{last_run.get('group_name', '—')} "
            f"{last_run.get('run_date', '—')} "
            f"{last_run.get('status', '—')}；未完成任务 {incomplete} 个"
        ),
    }

    return {"checks": checks, "warnings": _environment_warnings()}


@router.get("/system/startup")
def startup_checks(request: Request):
    """返回服务启动时保存的检查快照，不在 GET 请求中重新探测外部依赖。"""
    return {
        "checks": list(getattr(request.app.state, "startup_checks", [])),
        "error": str(getattr(request.app.state, "startup_check_error", "") or ""),
    }


@router.get("/system/recovery")
def recovery_info(settings: Settings = Depends(get_settings)):
    from app.v2.recovery import scan_incomplete, verify_output

    store = _store(settings)
    runs = store.list_runs()
    return {
        "incomplete": scan_incomplete(store, runs=runs),
        "integrity": verify_output(store, runs=runs),
    }


@router.post("/pipeline/retry-failed")
def retry_failed(
    body: RetryBody | None = None,
    settings: Settings = Depends(get_settings),
):
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.v2.recovery import scan_incomplete

    body = body or RetryBody()
    if body.run_date is not None:
        _validate_run_date(body.run_date)
    if body.group_id:
        pipeline = DailyPipeline()
        result = pipeline.force_generate(body.group_id, body.run_date)
        return {
            "results": [
                {
                    "group_id": body.group_id,
                    "status": result.get("status"),
                    "detail": result.get("error") or "",
                }
            ]
        }

    incomplete = scan_incomplete(_store(settings), body.run_date)
    results: list[dict] = []
    pipeline = None
    for run in incomplete:
        group_name = run["group_name"]
        if run.get("recovery_type") == "manual_review":
            error_type = run.get("error_type") or RUN_STATE_CORRUPT
            results.append(
                {
                    "group_name": group_name,
                    "status": "blocked",
                    "error_type": error_type,
                    "detail": (
                        "AI 调用结果未知，需人工复核"
                        if error_type == "PROMPT_RESULT_UNKNOWN"
                        else "微信发送结果未知，需人工核对后消歧"
                        if error_type == "SEND_RESULT_UNKNOWN"
                        else "运行状态文件损坏，需人工复核"
                    ),
                }
            )
            continue
        if pipeline is None:
            pipeline = DailyPipeline()

        from sqlmodel import Session, select

        from app.db import repository as repo
        from app.db.models import Group

        with Session(repo.engine) as session:
            group = session.exec(
                select(Group).where(Group.display_name == group_name)
            ).first()
        if group is None:
            results.append(
                {
                    "group_name": group_name,
                    "status": "skipped",
                    "detail": "群不存在/已停用",
                }
            )
            continue
        if run.get("recovery_type") == "send":
            result = pipeline.force_send(group.id, run["run_date"])
        else:
            result = pipeline.force_generate(group.id, run["run_date"])
        results.append(
            {
                "group_name": group_name,
                "status": result.get("status"),
                "detail": result.get("error") or "",
            }
        )
    return {"results": results}


def _environment_warnings() -> list[str]:
    return [
        "休眠/锁屏风险：GroupBrief 自动发送依赖桌面会话。请保持电脑开机、不休眠、不锁屏；"
        "Windows 电源设置请关闭自动休眠与自动锁屏。"
    ]


@router.post("/pipeline/generate")
def pipeline_generate(body: PipelineGenerateBody):
    from app.pipeline.daily_pipeline import DailyPipeline
    from app.services.generation_runtime import GenerationBusyError

    if body.run_date is not None:
        _validate_run_date(body.run_date)
    pipeline = DailyPipeline(dry_run=False)
    try:
        if body.group_id:
            result = pipeline.force_generate(
                body.group_id,
                body.run_date,
                refresh_messages=body.refresh_messages,
            )
            return {"results": [result]}
        results = pipeline.generate_all(
            run_date=body.run_date,
            force=body.force,
            refresh_messages=body.refresh_messages,
        )
    except GenerationBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    if body.run_date is not None:
        _validate_run_date(body.run_date)
    pipeline = DailyPipeline(dry_run=False)
    result = pipeline.force_send(
        body.group_id,
        body.run_date,
        confirm_regenerated=body.confirm_regenerated,
        confirm_late_send=body.confirm_late_send,
    )
    return {"result": result}


@router.post("/pipeline/resolve-send-unknown")
def pipeline_resolve_send_unknown(body: ResolveSendUnknownBody):
    from app.pipeline.daily_pipeline import DailyPipeline

    _validate_run_date(body.run_date)
    result = DailyPipeline(dry_run=False).resolve_send_unknown(
        body.group_id,
        body.run_date,
        resolution=body.resolution,
        expected_send_unknown_at=body.expected_send_unknown_at,
    )
    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result)
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result)
    return {"result": result}


@router.post("/pipeline/resolve-manual-send")
def pipeline_resolve_manual_send(body: ResolveManualSendBody):
    """记录人工核对结论；该接口不会调用微信 Sender。"""
    from app.pipeline.daily_pipeline import DailyPipeline

    _validate_run_date(body.run_date)
    result = DailyPipeline(dry_run=False).resolve_manual_send(
        body.group_id,
        body.run_date,
        resolution=body.resolution,
        expected_updated_at=body.expected_updated_at,
    )
    if result.get("status") == "conflict":
        raise HTTPException(status_code=409, detail=result)
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result)
    return {"result": result}
