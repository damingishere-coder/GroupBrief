from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.config.settings import Settings
from app.sender.wechat_native import (
    NativeActionResult,
    OcrLine,
    WechatNativeSender,
    WindowsWechatDriver,
    _title_matches,
)


class FakeNativeDriver:
    def __init__(self, *, verify=True, text=True, image=True):
        self.verify = verify
        self.text = text
        self.image = image
        self.calls: list[tuple[str, str]] = []

    def health_check(self):
        return True, "mock ready"

    def open_and_verify(self, target: str):
        self.calls.append(("verify", target))
        return self.verify, "目标唯一" if self.verify else "目标歧义"

    def paste_text(self, text: str):
        self.calls.append(("text", text))
        return self.text, "文字成功" if self.text else "文字失败"

    def paste_image(self, image_path: Path):
        self.calls.append(("image", str(image_path)))
        return self.image, "图片成功" if self.image else "图片失败"


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        wechat_native_mutex_timeout_seconds=1,
    )


def test_title_match_only_accepts_exact_name_or_member_count():
    assert _title_matches("测试群", "测试群")
    assert _title_matches("测试群（128）", "测试群")
    assert _title_matches("测试群 (128)", "测试群")
    assert not _title_matches("测试群公告", "测试群")
    assert not _title_matches("另一个测试群", "测试群")


def test_verify_target_fails_closed_on_ambiguous_result(tmp_path):
    driver = FakeNativeDriver(verify=False)
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)
    ok, detail = sender.verify_target("文件传输助手")
    assert not ok
    assert "歧义" in detail
    assert driver.calls == [("verify", "文件传输助手")]


def test_bundle_verifies_once_then_sends_text_and_image(tmp_path):
    image = tmp_path / "daily_image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    driver = FakeNativeDriver()
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)

    text_result, image_result = sender.send_bundle("文件传输助手", "唯一标记", image)

    assert text_result.success
    assert image_result is not None and image_result.success
    assert [call[0] for call in driver.calls] == ["verify", "text", "image"]


def test_bundle_stops_when_target_verification_fails(tmp_path):
    image = tmp_path / "daily_image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    driver = FakeNativeDriver(verify=False)
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)

    text_result, image_result = sender.send_bundle("重名群", "不会发送", image)

    assert not text_result.success
    assert image_result is None
    assert [call[0] for call in driver.calls] == ["verify"]


def test_bundle_reports_image_failure_after_text_success(tmp_path):
    image = tmp_path / "daily_image.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    driver = FakeNativeDriver(image=False)
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)

    text_result, image_result = sender.send_bundle("文件传输助手", "文字", image)

    assert text_result.success
    assert image_result is not None and not image_result.success
    assert "图片失败" in image_result.detail


def test_native_result_preserves_submitted_but_unknown_state(tmp_path):
    class UnknownDriver(FakeNativeDriver):
        def paste_text(self, text: str):
            self.calls.append(("text", text))
            return NativeActionResult(False, "UI 未确认", True, "unknown", True)

    sender = WechatNativeSender(_settings(tmp_path), driver=UnknownDriver())

    result = sender.send_text("文件传输助手", "文字")

    assert result.success is False
    assert result.submitted is True
    assert result.outcome_unknown is True
    assert result.verification_level == "unknown"


def test_health_report_rejects_missing_chinese_ocr(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    monkeypatch.setattr("app.sender.wechat_native.os.name", "nt")
    monkeypatch.setattr(driver, "_imports", lambda: None)
    monkeypatch.setattr(driver, "_desktop_unlocked", lambda: True)
    monkeypatch.setattr(driver, "_check_clipboard", lambda: None)
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])

    async def english_ocr():
        return "en-US"

    monkeypatch.setattr(driver, "_ocr_language", english_ocr)

    report = driver.health_report()

    assert report["ok"] is False
    assert report["ocr"]["ok"] is False
    assert "中文" in report["ocr"]["detail"]


def test_target_search_overwrites_stale_query_before_paste(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    hotkeys: list[tuple[str, str]] = []
    screenshots = iter(
        [
            [OcrLine("其他会话", 10, 10, 100, 20)],
            [
                OcrLine("搜索网络结果", 10, 5, 100, 20),
                OcrLine("文件传输助手", 10, 200, 100, 20),
            ],
            [OcrLine("文件传输助手", 10, 10, 100, 20)],
        ]
    )
    monkeypatch.setattr(driver, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: True)
    monkeypatch.setattr(driver, "_hotkey", lambda modifier, key: hotkeys.append((modifier, key)))
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: next(screenshots))
    monkeypatch.setattr(driver, "_click", lambda x, y: None)
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, _ = driver.open_and_verify("文件传输助手")

    assert ok is True
    assert hotkeys[:2] == [("ctrl", "f"), ("ctrl", "a")]


def test_submission_verification_requires_composer_to_return_near_empty():
    before = Image.new("RGB", (120, 80), "white")
    staged = before.copy()
    ImageDraw.Draw(staged).rectangle((10, 10, 80, 60), fill="black")
    still_staged = before.copy()
    ImageDraw.Draw(still_staged).rectangle((20, 10, 90, 60), fill="black")
    chat_before = Image.new("RGB", (120, 80), "white")
    chat_after = chat_before.copy()
    ImageDraw.Draw(chat_after).rectangle((10, 10, 30, 30), fill="black")

    ok, _ = WindowsWechatDriver._verify_submission(
        before, staged, still_staged, chat_before, chat_after
    )
    assert ok is False

    ok, _ = WindowsWechatDriver._verify_submission(
        before, staged, before.copy(), chat_before, chat_after
    )
    assert ok is True
