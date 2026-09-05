"""P5 测试：文件输出结构 + V2 Handoff。"""

import json
from datetime import datetime

from sqlmodel import Session

from app.config.settings import Settings, get_settings
from app.db import repository as repo
from app.db.models import Group
from app.scheduler.calendar_rules import get_report_window
from app.services.handoff_service import safe_dir_name
from app.services.history_service import HistoryService
from app.services.prompt_service import PromptService
from app.services.report_service import ReportService

settings = get_settings()
settings.ensure_dirs()
repo.init_db(settings)


def _test_report_service() -> ReportService:
    test_settings = Settings(
        _env_file=None,
        allow_test_providers=True,
        history_provider_primary="mock",
        history_provider_fallback="",
        history_provider_mock_enabled=True,
        summary_provider_primary="deepseek",
        ai_api_key="",
    )
    return ReportService(
        history=HistoryService(test_settings),
        prompt=PromptService(test_settings),
    )


def _get_or_create_group(session: Session, display_name: str, wechat_group_id: str) -> Group:
    group = repo.find_group_by_wechat_id(
        session,
        wechat_group_id,
        include_deleted=False,
    )
    if group is not None:
        return group
    return repo.save_group(
        session,
        Group(display_name=display_name, wechat_group_id=wechat_group_id),
    )


def test_safe_dir_name():
    assert safe_dir_name("示例UED-4群") == "示例UED-4群"
    assert ":" not in safe_dir_name("a:b/c*d?e")
    assert "/" not in safe_dir_name("a/b")
    assert safe_dir_name("") == "group"
    assert safe_dir_name(".") == "group"
    assert safe_dir_name("..") == "group"


def test_generate_writes_files():
    with Session(repo.engine) as session:
        group = _get_or_create_group(session, "示例UED-4群", "group-a")
        service = _test_report_service()
        run = service.generate(session, group=group, report_date="2026-08-13", force=True)
        assert run.status == "success"

        from sqlmodel import select

        from app.db.models import GroupRun, Report

        group_run = session.exec(select(GroupRun).where(GroupRun.run_id == run.id)).first()
        report = repo.get_report_by_group_run(session, group_run.id)
        assert report is not None
        assert report.ranking_file

        day_dir = settings.output_dir / "2026-08-13"
        group_dir = day_dir / "示例UED-4群"
        assert (group_dir / "ranking.txt").exists()
        assert (group_dir / "image_prompt.txt").exists()
        assert (group_dir / "meta.json").exists()
        assert (group_dir / "normalized_messages.json").exists()
        assert (group_dir / "handoff.json").exists()

        # ranking.txt 内容（8-13 为周四，统计 8-12 周三）
        ranking_text = (group_dir / "ranking.txt").read_text(encoding="utf-8")
        assert "消息统计" in ranking_text
        assert "时间起：2026-08-12 00:00:00" in ranking_text

        # handoff.json 结构（V2 预留）
        handoff = json.loads((group_dir / "handoff.json").read_text(encoding="utf-8"))
        assert handoff["version"] == 1
        assert handoff["date"] == "2026-08-13"
        assert handoff["group_id"] == "group-a"
        assert handoff["ranking_file"] == "ranking.txt"
        assert handoff["prompt_file"] == "image_prompt.txt"
        assert handoff["poster_file"] is None
        assert handoff["status"] == "prompt_ready"

        # meta.json 数据正确
        meta = json.loads((group_dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["message_count"] > 0
        assert meta["speaker_count"] > 0
        assert meta["provider"] == "mock"


def test_two_groups_isolated():
    with Session(repo.engine) as session:
        # 本用例必须可独立运行，不能依赖 test_generate_writes_files 先创建 A 群。
        first_group = _get_or_create_group(session, "示例UED-4群", "group-a")
        second_group = _get_or_create_group(session, "产品经理交流群", "group-b")
        service = _test_report_service()
        first_run = service.generate(
            session,
            group=first_group,
            report_date="2026-08-13",
            trigger_type="auto",
            force=True,
        )
        second_run = service.generate(
            session,
            group=second_group,
            report_date="2026-08-13",
            trigger_type="auto",
            force=True,
        )
        assert first_run.status == "success"
        assert second_run.status == "success"

        day_dir = settings.output_dir / "2026-08-13"
        dirs = {p.name for p in day_dir.iterdir() if p.is_dir()}
        assert "示例UED-4群" in dirs
        assert "产品经理交流群" in dirs

        a = json.loads((day_dir / "示例UED-4群" / "meta.json").read_text(encoding="utf-8"))
        b = json.loads((day_dir / "产品经理交流群" / "meta.json").read_text(encoding="utf-8"))
        assert a["group_id"] != b["group_id"]
        assert a["message_count"] > 0 and b["message_count"] > 0
