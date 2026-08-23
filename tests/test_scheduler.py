"""P7 测试：Scheduler 配置与任务函数。"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.scheduler.manager import (
    _parse_generate_time,
    _schedule_startup_catchup,
    get_scheduler,
    start_scheduler,
    stop_scheduler,
)


def test_invalid_generate_time_falls_back_to_0015():
    assert _parse_generate_time("not-a-time") == time(0, 15)


def test_scheduler_jobs_configured():
    from app.config.settings import Settings

    # 这里只验证 Cron 注册；禁止启动补偿在测试后台触发真实生成链路。
    settings = Settings(_env_file=None, schedule_startup_catchup_enabled=False)
    start_scheduler(settings)
    scheduler = get_scheduler()
    assert scheduler is not None
    jobs = scheduler.get_jobs()
    ids = [j.id for j in jobs]
    assert "daily_v2_generate_email" in ids
    assert "send_wechat_due" in ids
    assert "generate_daily" not in ids
    assert "send_daily_email" not in ids
    for job in jobs:
        if job.id == "daily_v2_generate_email":
            assert job.name == "DailyV2GenerateAndEmail"
            fields = {field.name: str(field) for field in job.trigger.fields}
            assert fields["hour"] == "0"
            assert fields["minute"] == "15"
        if job.id == "send_wechat_due":
            assert job.name == "SendWechatDue"
    assert start_scheduler(settings) is scheduler
    stop_scheduler()
    assert get_scheduler() is None


def test_generate_job_runs_every_day():
    """兼容自动任务每天都执行（使用 mock 数据）。"""
    from sqlmodel import Session, select

    from app.config.settings import get_settings
    from app.db import repository as repo
    from app.db.models import Run
    from app.scheduler.generate_job import run_generate_job

    settings = get_settings()
    settings.ensure_dirs()
    repo.init_db(settings)  # 该测试不依赖其他测试文件的副作用，独立初始化
    result = run_generate_job()
    assert result["status"] in ("success", "partial", "failed")


def test_daily_v2_job_persists_generation_and_email_idempotency(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(_env_file=None, email_enabled=False, email_smtp_host="")
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    calls = []

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings

        def generate_all(self, run_date, acquire_lock=True):
            calls.append(run_date)
            return [{"group_name": "测试群", "status": "ready_to_send"}]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", FakePipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])

    first = daily.run_daily_v2_job("2026-08-23", settings=settings)  # 周日也运行
    second = daily.run_daily_v2_job("2026-08-23", settings=settings)
    state = TempState(tmp_path).load("2026-08-23")

    assert first["status"] == "success"
    assert first["email_status"] == "skipped_disabled"
    assert second["status"] == "already_completed"
    assert calls == ["2026-08-23"]
    assert state["generation_completed_at"]
    assert state["email_completed_at"]


def test_daily_v2_job_resumes_interrupted_generation_without_email(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(_env_file=None, email_enabled=True, email_smtp_host="smtp.example.com")
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    state.update("2026-08-21", generation_started_at="2026-08-21T00:15:00+08:00")
    calls = []

    class FakePipeline:
        def __init__(self, settings):
            self.settings = settings

        def generate_all(self, run_date, acquire_lock=True):
            calls.append((run_date, acquire_lock))
            return [{"group_name": "测试群", "status": "ready_to_send"}]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", FakePipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])

    result = daily.run_daily_v2_job("2026-08-21", settings=settings, skip_email=True)
    saved = state.load("2026-08-21")

    assert result["status"] == "success"
    assert result["email_status"] == "skipped_by_request"
    assert calls == [("2026-08-21", False)]
    assert saved["generation_completed_at"]
    assert saved["generation_resume_count"] == 1
    assert saved["generation_hold"] is False
    assert saved["generation_error"] == ""


def test_daily_v2_job_exception_keeps_generation_incomplete_for_startup_resume(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(_env_file=None, email_enabled=False, email_smtp_host="")
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class BrokenPipeline:
        def __init__(self, settings):
            pass

        def generate_all(self, run_date, acquire_lock=True):
            raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", BrokenPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])

    result = daily.run_daily_v2_job("2026-08-24", settings=settings, skip_email=True)
    state = TempState(tmp_path).load("2026-08-24")

    assert result["status"] == "failed"
    assert "generation_completed_at" not in state
    assert state["generation_status"] == "interrupted"
    assert state["generation_hold"] is True


def test_startup_catchup_is_added_only_when_today_is_incomplete(monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import manager

    settings = Settings(_env_file=None, schedule_startup_catchup_enabled=True)
    captured = []

    class FakeScheduler:
        def add_job(self, *args, **kwargs):
            captured.append((args, kwargs))

    class IncompleteState:
        def __init__(self, output_root):
            pass

        def load(self, run_date):
            return {"run_date": run_date}

    monkeypatch.setattr(manager, "DailyScheduleState", IncompleteState)
    before = datetime(2026, 8, 21, 0, 14, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert _schedule_startup_catchup(FakeScheduler(), settings, now=before) is False
    assert captured == []

    now = datetime(2026, 8, 21, 0, 15, tzinfo=ZoneInfo("Asia/Shanghai"))

    added = _schedule_startup_catchup(FakeScheduler(), settings, now=now)

    assert added is True
    assert captured[0][1]["id"] == "daily_v2_startup_catchup"
    assert captured[0][1]["kwargs"] == {"skip_email": True}

    class CompletedState(IncompleteState):
        def load(self, run_date):
            return {"run_date": run_date, "generation_completed_at": "done"}

    monkeypatch.setattr(manager, "DailyScheduleState", CompletedState)
    assert _schedule_startup_catchup(FakeScheduler(), settings, now=now) is False
