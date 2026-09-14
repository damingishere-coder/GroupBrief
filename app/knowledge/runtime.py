"""Best-effort lifecycle. Failure of the optional child never fails startup."""
import logging
import os
import subprocess
import sys
from app.config.settings import PROJECT_ROOT


def start_worker(settings):
    if not settings.knowledge_enabled or settings.scheduler_owner != "fastapi" or os.environ.get("GROUPBRIEF_NO_SCHEDULER") == "1":
        return None
    try:
        from app.knowledge.db import connect
        with connect(settings.db_path):
            pass
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{settings.db_path.as_posix()}",
               "OUTPUT_ROOT_OVERRIDE": str(settings.output_dir), "KNOWLEDGE_ENABLED": "true",
               "APP_TIMEZONE": settings.app_timezone}
        return subprocess.Popen([sys.executable, "-m", "app.knowledge.worker", "--parent-pid", str(os.getpid())],
            env=env, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception:
        logging.getLogger(__name__).exception("知识库未启动，日报服务继续运行")
        return None


def stop_worker(process):
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            logging.getLogger(__name__).warning("知识 worker 已退出")
