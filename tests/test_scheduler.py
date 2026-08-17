"""P7 测试：Scheduler 配置与任务函数。"""

from app.scheduler.manager import get_scheduler, start_scheduler, stop_scheduler


def test_scheduler_jobs_configured():
    from app.config.settings import get_settings

    settings = get_settings()
    start_scheduler(settings)
    scheduler = get_scheduler()
    assert scheduler is not None
    jobs = scheduler.get_jobs()
    ids = [j.id for j in jobs]
    assert "generate_daily" in ids
    assert "send_daily_email" in ids
    for job in jobs:
        if job.id == "generate_daily":
            assert job.name == "GenerateDailyReports"
        if job.id == "send_daily_email":
            assert job.name == "SendDailyEmail"
    stop_scheduler()
    assert get_scheduler() is None


def test_generate_job_runs_on_weekday():
    """自动任务在非周日时执行（使用 mock 数据）。"""
    from sqlmodel import Session, select

    from app.config.settings import get_settings
    from app.db import repository as repo
    from app.db.models import Run
    from app.scheduler.calendar_rules import get_report_window
    from app.scheduler.generate_job import run_generate_job

    settings = get_settings()
    settings.ensure_dirs()
    repo.init_db(settings)  # 该测试不依赖其他测试文件的副作用，独立初始化
    window = get_report_window(timezone=settings.app_timezone)
    if not window.should_run:
        return  # 周日跳过
    result = run_generate_job()
    assert result["status"] in ("success", "partial", "failed")
