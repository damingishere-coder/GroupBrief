"""GroupBrief FastAPI 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir)
    repository.init_db(settings)
    app.state.settings = settings
    if os.environ.get("GROUPBRIEF_NO_SCHEDULER", "") != "1":
        from app.scheduler.manager import start_scheduler

        start_scheduler(settings)
    yield
    if os.environ.get("GROUPBRIEF_NO_SCHEDULER", "") != "1":
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
