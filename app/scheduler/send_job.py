"""分钟级微信待发送扫描任务。"""

from __future__ import annotations

from app.config.settings import get_settings
from app.core.logging import get_logger
from app.pipeline.daily_pipeline import DailyPipeline

logger = get_logger("groupbrief.scheduler")


def run_send_due_job() -> None:
    """只处理已显式启用、到时、未发送且未被人工审核拦截的运行。"""
    try:
        results = DailyPipeline(settings=get_settings()).send_due()
        if results:
            logger.info("微信 send_due 扫描结果：%s", results)
    except Exception:
        logger.exception("微信 send_due 调度异常")
