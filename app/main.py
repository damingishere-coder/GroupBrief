"""GroupBrief FastAPI 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import email, files, groups, logs, reports, runs, settings, system
from app.api import v2_templates, v2_ui
from app.config.settings import PROJECT_ROOT, get_settings
from app.core.logging import setup_logging
from app.db import repository

APP_VERSION = "1.0.0"


def _should_start_scheduler(settings) -> bool:
    return (
        settings.scheduler_owner == "fastapi"
        and os.environ.get("GROUPBRIEF_NO_SCHEDULER", "") != "1"
    )


def _capture_startup_checks(settings, runner=None) -> tuple[list[dict], str]:
    if runner is None:
        from app.core.startup_check import run_startup_checks

        runner = run_startup_checks
    try:
        return runner(settings), ""
    except Exception as exc:
        logging.getLogger("app").exception("启动检查执行失败")
        detail = str(exc)[:200]
        return [
            {
                "name": "启动检查执行",
                "ok": False,
                "status": "ERROR",
                "detail": detail,
            }
        ], detail


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir)
    if settings.legacy_v1_write_mode == "maintenance":
        logging.getLogger("groupbrief.legacy_v1").warning(
            "旧 V1 写入 maintenance 模式已启用；正式生成与发送仍应使用 V2"
        )
    else:
        logging.getLogger("groupbrief.legacy_v1").info(
            "旧 V1 写入已冻结为只读模式"
        )
    repository.init_db(settings)
    app.state.settings = settings
    # P9：启动检查（记录日志，不阻止启动）
    app.state.startup_checks, app.state.startup_check_error = (
        _capture_startup_checks(settings)
    )
    # P9：日志轮转清理已在 setup_logging 中执行
    scheduler_started = _should_start_scheduler(settings)
    app.state.scheduler_owner = settings.scheduler_owner
    app.state.scheduler_active = scheduler_started
    if scheduler_started:
        from app.scheduler.manager import start_scheduler

        start_scheduler(settings)
    yield
    if scheduler_started:
        from app.scheduler.manager import stop_scheduler

        stop_scheduler()


app = FastAPI(title="GroupBrief", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(groups.router)
app.include_router(reports.router)
app.include_router(runs.router)
app.include_router(settings.router)
app.include_router(files.router)
app.include_router(email.router)
app.include_router(logs.router)
app.include_router(v2_templates.router)
app.include_router(v2_ui.router)


@app.get("/api/version")
def version():
    return {"name": "GroupBrief", "version": APP_VERSION}


def _mount_frontend(app: FastAPI) -> None:
    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")


_mount_frontend(app)
