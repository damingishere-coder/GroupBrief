"""V2 逐群邮件测试：所有 SMTP 均使用内存 fake，不触发真实网络。"""

import json
from types import SimpleNamespace

from scripts import send_daily_email as mail_script


def _settings(output_dir, *, enabled=True):
    return SimpleNamespace(
        output_dir=output_dir,
        email_enabled=enabled,
        email_recipient="recipient@example.com",
        email_from="sender@example.com",
        email_smtp_host="smtp.example.com",
        email_smtp_port=465,
        email_smtp_user="sender@example.com",
        email_smtp_password="not-used-by-fake",
        email_use_ssl=True,
    )


def _group(name, image_enabled=False):
    return SimpleNamespace(
        display_name=name,
        wechat_group_name=name,
        image_enabled=image_enabled,
    )


def _write_group(output_dir, name, run_date, ranking, *, image_enabled=False, image=None):
    group_dir = output_dir / mail_script.safe_dir_name(name) / run_date
    group_dir.mkdir(parents=True)
    (group_dir / "ranking.txt").write_text(ranking, encoding="utf-8")
    (group_dir / "run.json").write_text(
        json.dumps(
            {
                "period_start": "2026-08-20 00:00:00",
                "period_end": "2026-08-20 23:59:59",
                "image_enabled": image_enabled,
            }
        ),
        encoding="utf-8",
    )
    if image is not None:
        (group_dir / "daily_image.png").write_bytes(image)
    return group_dir


def test_collect_group_inputs_skips_empty_and_invalid_image(tmp_path):
    run_date = "2026-08-21"
    _write_group(tmp_path, "正常群", run_date, "排行榜 A", image_enabled=False)
    _write_group(tmp_path, "缺图群", run_date, "排行榜 B", image_enabled=True)
    _write_group(tmp_path, "空文本群", run_date, "   ", image_enabled=False)

    settings = _settings(tmp_path)
    blocks, skipped = mail_script.collect_group_inputs(
        settings,
        [_group("正常群"), _group("缺图群", image_enabled=True), _group("空文本群")],
        run_date,
    )

    assert [block.group_name for block in blocks] == ["正常群"]
    assert any("缺图群" in item for item in skipped)
    assert any("空文本群" in item for item in skipped)


def test_build_message_uses_raw_ranking_and_one_image_attachment(tmp_path):
    run_date = "2026-08-21"
    image = b"\x89PNG\r\n\x1a\nminimal-png"
    group_dir = _write_group(
        tmp_path,
        "示例群 A",
        run_date,
        "===== 示例群 A =====\n【发言排行榜】\n1. Alice【3】",
        image_enabled=True,
        image=image,
    )
    settings = _settings(tmp_path)
    blocks, skipped = mail_script.collect_group_inputs(
        settings,
        [_group("示例群 A", image_enabled=True)],
        run_date,
    )

    assert not skipped
    message = mail_script.build_message(blocks[0], settings)
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == blocks[0].ranking_text
    assert "示例群 A" in message["Subject"]
    assert "2026-08-20" in message["Subject"]
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_content_type() == "image/png"
    assert "示例群-A-日报图片.png" == attachments[0].get_filename()
    assert str(group_dir) not in attachments[0].get_filename()


def test_collect_group_inputs_prefers_current_image_setting_over_stale_run_snapshot(tmp_path):
    run_date = "2026-08-21"
    image = b"\x89PNG\r\n\x1a\nminimal-png"
    _write_group(
        tmp_path,
        "后来开启图片的群",
        run_date,
        "排行榜",
        image_enabled=False,
        image=image,
    )

    blocks, skipped = mail_script.collect_group_inputs(
        _settings(tmp_path),
        [_group("后来开启图片的群", image_enabled=True)],
        run_date,
    )

    assert not skipped
    assert len(blocks) == 1
    assert blocks[0].image_enabled is True
    assert blocks[0].image_path is not None


def test_main_sends_each_group_and_continues_after_failure(tmp_path, monkeypatch):
    run_date = "2026-08-21"
    _write_group(tmp_path, "失败群", run_date, "排行榜 失败", image_enabled=False)
    _write_group(tmp_path, "成功群", run_date, "排行榜 成功", image_enabled=False)
    settings = _settings(tmp_path)
    groups = [_group("失败群"), _group("成功群")]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeSMTP:
        instances = []

        def __init__(self, *args, **kwargs):
            self.messages = []
            self.__class__.instances.append(self)

        def login(self, *args):
            return None

        def send_message(self, message):
            self.messages.append(message)
            if "失败群" in str(message["Subject"]):
                raise RuntimeError("fake failure")

        def quit(self):
            return None

    monkeypatch.setattr(mail_script, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_script.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "apply_db_settings", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "list_groups", lambda session, only_enabled=True: groups)
    monkeypatch.setattr(mail_script, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(mail_script.repo, "engine", object())
    monkeypatch.setattr(mail_script.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail_script.time, "sleep", lambda seconds: None)
    monkeypatch.setattr("sys.argv", ["send_daily_email.py", "--run-date", run_date])

    rc = mail_script.main()

    assert rc == 1
    sent_subjects = [
        str(message["Subject"])
        for instance in FakeSMTP.instances
        for message in instance.messages
        if "成功群" in str(message["Subject"])
    ]
    failed_attempts = [
        message
        for instance in FakeSMTP.instances
        for message in instance.messages
        if "失败群" in str(message["Subject"])
    ]
    assert len(sent_subjects) == 1
    assert len(failed_attempts) == 2


def test_send_quit_failure_does_not_retry(monkeypatch):
    settings = _settings(None)
    calls = {"connect": 0, "send": 0, "sleep": 0}

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            calls["connect"] += 1

        def login(self, *args):
            return None

        def send_message(self, message):
            calls["send"] += 1

        def quit(self):
            raise RuntimeError("fake quit failure")

    monkeypatch.setattr(mail_script.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail_script.time, "sleep", lambda seconds: calls.__setitem__("sleep", calls["sleep"] + 1))
    message = mail_script.EmailMessage()
    message["Subject"] = "测试群"

    ok, detail = mail_script._send_with_retry(message, settings)

    assert ok
    assert detail == ""
    assert calls == {"connect": 1, "send": 1, "sleep": 0}


def test_main_can_send_only_requested_group(tmp_path, monkeypatch):
    run_date = "2026-08-21"
    _write_group(tmp_path, "目标群", run_date, "排行榜 目标群", image_enabled=False)
    _write_group(tmp_path, "其他群", run_date, "排行榜 其他群", image_enabled=False)
    settings = _settings(tmp_path)
    groups = [_group("目标群"), _group("其他群")]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeSMTP:
        messages = []

        def __init__(self, *args, **kwargs):
            pass

        def login(self, *args):
            return None

        def send_message(self, message):
            self.__class__.messages.append(message)

        def quit(self):
            return None

    monkeypatch.setattr(mail_script, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_script.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "apply_db_settings", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "list_groups", lambda session, only_enabled=True: groups)
    monkeypatch.setattr(mail_script, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(mail_script.repo, "engine", object())
    monkeypatch.setattr(mail_script.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(
        "sys.argv",
        ["send_daily_email.py", "--run-date", run_date, "--group", "目标群"],
    )

    assert mail_script.main() == 0
    assert len(FakeSMTP.messages) == 1
    assert "目标群" in str(FakeSMTP.messages[0]["Subject"])


def test_dry_run_does_not_require_smtp_or_connect(tmp_path, monkeypatch, capsys):
    run_date = "2026-08-21"
    _write_group(tmp_path, "预览群", run_date, "排行榜 预览", image_enabled=False)
    settings = _settings(tmp_path, enabled=False)
    group = _group("预览群")

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fail_smtp(*args, **kwargs):
        raise AssertionError("dry-run 不应连接 SMTP")

    monkeypatch.setattr(mail_script, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_script.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "apply_db_settings", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "list_groups", lambda session, only_enabled=True: [group])
    monkeypatch.setattr(mail_script, "Session", lambda engine: FakeSession())
    monkeypatch.setattr(mail_script.repo, "engine", object())
    monkeypatch.setattr(mail_script.smtplib, "SMTP_SSL", fail_smtp)
    monkeypatch.setattr("sys.argv", ["send_daily_email.py", "--run-date", run_date, "--dry-run"])

    assert mail_script.main() == 0
    assert "预览群" in capsys.readouterr().out


def test_main_invalid_email_config_aborts_before_smtp(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    settings.email_recipient = ""
    smtp_calls = []

    def fail_smtp(*args, **kwargs):
        smtp_calls.append((args, kwargs))
        raise AssertionError("配置无效时不应连接 SMTP")

    monkeypatch.setattr(mail_script, "get_settings", lambda: settings)
    monkeypatch.setattr(mail_script.repo, "init_db", lambda settings: None)
    monkeypatch.setattr(mail_script.repo, "apply_db_settings", lambda settings: None)
    monkeypatch.setattr(mail_script.smtplib, "SMTP_SSL", fail_smtp)
    monkeypatch.setattr("sys.argv", ["send_daily_email.py", "--run-date", "2026-08-21"])

    assert mail_script.main() == 1
    assert "收件人" in capsys.readouterr().out
    assert not smtp_calls
