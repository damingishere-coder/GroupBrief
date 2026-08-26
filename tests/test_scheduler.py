"""P7 测试：Scheduler 配置与任务函数。"""

from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

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


def test_scheduler_owner_controls_fastapi_registration(monkeypatch):
    from app.config.settings import Settings
    from app.main import _should_start_scheduler

    monkeypatch.delenv("GROUPBRIEF_NO_SCHEDULER", raising=False)
    assert _should_start_scheduler(Settings(_env_file=None, scheduler_owner="fastapi")) is True
    assert _should_start_scheduler(Settings(_env_file=None, scheduler_owner="external")) is False
    assert _should_start_scheduler(Settings(_env_file=None, scheduler_owner="disabled")) is False
    monkeypatch.setenv("GROUPBRIEF_NO_SCHEDULER", "1")
    assert _should_start_scheduler(Settings(_env_file=None, scheduler_owner="fastapi")) is False


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

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    state.update(
        "2026-08-21",
        generation_started_at="2026-08-21T00:15:00+08:00",
        generation_status="running",
    )
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


def test_daily_v2_job_reports_busy_as_not_executed(monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily
    from app.services.generation_runtime import GenerationBusyError

    class BusyContext:
        def __enter__(self):
            raise GenerationBusyError("已有实例")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(daily, "_daily_mutex", lambda: BusyContext())

    result = daily.run_daily_v2_job(
        "2026-08-25",
        settings=Settings(_env_file=None),
        skip_email=True,
    )

    assert result["status"] == "already_running"
    assert result["outcome_status"] == "already_running"
    assert result["exit_code"] == 4


def test_partial_generation_stays_partial_even_when_email_succeeds(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
        email_send_partial_report=True,
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class PartialPipeline:
        def __init__(self, settings):
            pass

        def generate_all(self, run_date, acquire_lock=True):
            return [
                {"group_name": "群A", "status": "ready_to_send"},
                {"group_name": "群B", "status": "failed"},
            ]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", PartialPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="sent", stderr=""),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "partial"
    assert result["outcome_status"] == "partial"
    assert result["exit_code"] == 2
    assert result["email_status"] == "sent"


def test_completed_partial_run_reconciles_thread_image_and_emails_only_recovered_group(
    tmp_path, monkeypatch
):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily
    from app.v2.constants import IMAGE_GENERATION_FAILED

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    run_date = "2026-08-25"
    state = TempState(tmp_path)
    state.update(
        run_date,
        generation_started_at="2026-08-25T00:15:00+08:00",
        generation_completed_at="2026-08-25T00:20:00+08:00",
        generation_status="partial",
        generation_results=[
            {"group_name": "群A", "status": "ready_to_send"},
            {
                "group_name": "群B",
                "status": "failed",
                "error_type": IMAGE_GENERATION_FAILED,
                "detail": "原始失败必须保留",
            },
        ],
        email_started_at="2026-08-25T00:21:00+08:00",
        email_completed_at="2026-08-25T00:22:00+08:00",
        email_status="sent",
        email_detail="群A 已发送，群B 当时没有图片且不在发送集合",
    )
    prompt_path = tmp_path / "群B" / run_date / "image_prompt.txt"
    prompt_path.parent.mkdir(parents=True)
    prompt_path.write_text("test prompt", encoding="utf-8")
    generation_calls = []
    commands = []

    class FakeGenerator:
        def can_reconcile_without_generation(self, candidate_prompt, job_id):
            assert candidate_prompt == prompt_path
            assert job_id == "recovery_job_123"
            return True

    class FakeStore:
        def load_run(self, group_name, candidate_date):
            assert (group_name, candidate_date) == ("群B", run_date)
            return {"image_job": {"job_id": "recovery_job_123"}}

        def prompt_path(self, group_name, candidate_date):
            assert (group_name, candidate_date) == ("群B", run_date)
            return prompt_path

    class RecoveryPipeline:
        def __init__(self, settings):
            self.image_generator = FakeGenerator()
            self.store = FakeStore()

        def _load_groups(self):
            return [SimpleNamespace(id=2, display_name="群B", wechat_group_name="")]

        def generate_all(self, run_date, group_ids=None, force=False, acquire_lock=True):
            generation_calls.append((run_date, group_ids, force, acquire_lock))
            return [
                {
                    "group_name": "群B",
                    "status": "ready_to_send",
                    "receipt_source": "codex_thread_scan",
                    "recovery_status": "recovered_from_result_unknown",
                    "codex_thread_id": "thread-12345678",
                }
            ]

    def fake_subprocess_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="sent", stderr="")

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", RecoveryPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(daily.subprocess, "run", fake_subprocess_run)

    result = daily.run_daily_v2_job(run_date, settings=settings)
    saved = state.load(run_date)

    assert result["status"] == "success"
    assert result["email_status"] == "sent"
    assert generation_calls == [(run_date, [2], False, False)]
    assert commands and commands[0][-2:] == ["--group", "群B"]
    assert commands[0].count("--group") == 1
    assert saved["generation_original_status"] == "partial"
    assert saved["generation_history"][-1]["results"][1]["detail"] == "原始失败必须保留"
    assert saved["generation_results"][1]["receipt_source"] == "codex_thread_scan"
    assert saved["email_history"][-1]["status"] == "sent"
    assert saved["email_recovery_required"] is False
    assert saved["email_recovered_at"]


def test_invalid_email_config_fails_before_subprocess(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="",
        email_from="from@example.com",
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class SuccessPipeline:
        def __init__(self, settings):
            pass

        def generate_all(self, run_date, acquire_lock=True):
            return [{"group_name": "群A", "status": "ready_to_send"}]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", SuccessPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("配置无效时不得启动邮件子进程"),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "partial"
    assert result["error_type"] == "EMAIL_PROVIDER_CONFIG_INVALID"
    assert result["email_status"] == "failed_config"


def test_no_groups_is_not_run_and_never_calls_email(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    class EmptyPipeline:
        def __init__(self, settings):
            pass

        def generate_all(self, run_date, acquire_lock=True):
            return [{"status": "no_groups", "reason": "无启用群"}]

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily, "DailyPipeline", EmptyPipeline)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("no_groups 不得调用邮件"),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "not_run"
    assert result["exit_code"] == 5
    assert result["email_status"] == "skipped_generation_not_successful"


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([{"status": "ready_to_send"}], "success"),
        ([{"status": "no_groups"}], "not_run"),
        ([{"status": "blocked"}], "blocked"),
        ([{"status": "held"}], "blocked"),
        ([{"status": "unexpected"}], "failed"),
    ],
)
def test_generation_status_fails_closed(results, expected):
    from app.scheduler.daily_v2_job import _generation_status

    assert _generation_status(results) == expected


def test_apscheduler_daily_wrapper_raises_for_partial(monkeypatch):
    from app.scheduler import manager
    from app.scheduler.outcome import SchedulerOutcomeError

    monkeypatch.setattr(
        manager,
        "run_daily_v2_job",
        lambda *args, **kwargs: {"status": "partial", "outcome_status": "partial", "exit_code": 2},
    )

    with pytest.raises(SchedulerOutcomeError):
        manager.run_scheduled_daily_v2_job("2026-08-25")


def test_send_due_scheduler_allows_empty_scan_but_rejects_failure(monkeypatch):
    from app.scheduler import send_job
    from app.scheduler.outcome import SchedulerOutcomeError

    class EmptyPipeline:
        def __init__(self, settings):
            pass

        def send_due(self):
            return []

    monkeypatch.setattr(send_job, "DailyPipeline", EmptyPipeline)
    assert send_job.run_send_due_job()["outcome_status"] == "not_run"

    class FailedPipeline(EmptyPipeline):
        def send_due(self):
            return [{"group_name": "群A", "status": "failed"}]

    monkeypatch.setattr(send_job, "DailyPipeline", FailedPipeline)
    with pytest.raises(SchedulerOutcomeError):
        send_job.run_send_due_job()

    class BrokenPipeline(EmptyPipeline):
        def send_due(self):
            raise RuntimeError("simulated send scan failure")

    monkeypatch.setattr(send_job, "DailyPipeline", BrokenPipeline)
    with pytest.raises(RuntimeError, match="simulated send scan failure"):
        send_job.run_send_due_job()


def test_corrupt_scheduler_state_blocks_generation_email_and_overwrite(tmp_path, monkeypatch):
    import pytest

    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(_env_file=None, email_enabled=True, email_smtp_host="smtp.example.com")
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    path = state.path("2026-08-25")
    path.parent.mkdir(parents=True)
    original = b"{broken"
    path.write_bytes(original)

    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(
        daily,
        "DailyPipeline",
        lambda settings: pytest.fail("损坏调度状态不得启动 Pipeline"),
    )
    monkeypatch.setattr(
        daily.repo,
        "init_db",
        lambda settings: pytest.fail("损坏调度状态应在数据库初始化前阻断"),
    )
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("损坏调度状态不得启动邮件子进程"),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "blocked"
    assert result["error_type"] == "SCHEDULER_STATE_CORRUPT"
    with pytest.raises(daily.ScheduleStateCorruptionError):
        state.update("2026-08-25", generation_status="running")
    assert path.read_bytes() == original


def test_scheduler_schema_corruption_is_not_treated_as_new_run(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(_env_file=None, email_enabled=False, email_smtp_host="")
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    path = state.path("2026-08-25")
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"run_date":"2026-08-25","generation_started_at":"2026-08-25T00:15:00+08:00",'
        '"generation_status":"running","generation_results":"not-a-list"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(daily, "DailyScheduleState", TempState)

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)

    assert result["status"] == "blocked"
    assert result["error_type"] == "SCHEDULER_STATE_CORRUPT"


def test_email_started_without_completion_remains_result_unknown(tmp_path, monkeypatch):
    from app.config.settings import Settings
    from app.scheduler import daily_v2_job as daily

    settings = Settings(
        _env_file=None,
        email_enabled=True,
        email_smtp_host="smtp.example.com",
        email_recipient="to@example.com",
        email_from="from@example.com",
    )
    real_state_class = daily.DailyScheduleState

    class TempState(real_state_class):
        def __init__(self, _output_root):
            super().__init__(tmp_path)

    state = TempState(tmp_path)
    state.update(
        "2026-08-25",
        generation_started_at="2026-08-25T00:15:00+08:00",
        generation_completed_at="2026-08-25T00:20:00+08:00",
        generation_status="success",
        generation_results=[],
        email_started_at="2026-08-25T08:30:00+08:00",
        email_status="running",
    )
    monkeypatch.setattr(daily, "DailyScheduleState", TempState)
    monkeypatch.setattr(daily.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(daily.repo, "apply_db_settings", lambda settings: [])
    monkeypatch.setattr(
        daily.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("结果未知时不得重发邮件")),
    )

    result = daily.run_daily_v2_job("2026-08-25", settings=settings)
    saved = state.load("2026-08-25")

    assert result["status"] == "blocked"
    assert result["error_type"] == "EMAIL_RESULT_UNKNOWN"
    assert saved["email_status"] == "unknown"
    assert saved["email_hold"] is True


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

    class CorruptState(IncompleteState):
        def load(self, run_date):
            return {
                "run_date": run_date,
                "state_status": "corrupt",
                "error_type": "SCHEDULER_STATE_CORRUPT",
            }

    monkeypatch.setattr(manager, "DailyScheduleState", CorruptState)
    captured.clear()
    assert _schedule_startup_catchup(FakeScheduler(), settings, now=now) is False
    assert captured == []
