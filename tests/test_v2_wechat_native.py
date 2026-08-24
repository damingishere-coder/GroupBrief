from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.config.settings import Settings
from app.sender.wechat_native import (
    NativeActionResult,
    OcrLine,
    WechatNativeSender,
    WindowsWechatDriver,
    _main_chat_horizontal_bounds,
    _selected_header_matches,
    _select_group_search_match,
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
    assert _title_matches("米游涩泛二次元同好摸鱼群2．3", "米游涩泛二次元同好摸鱼群2.3")
    assert _title_matches("米 游 涩 泛 二 次 元 同 好 摸 鱼 群 1，1（439）", "米游涩泛二次元同好摸鱼群1.1")
    assert _title_matches("Eason张UED-4群", "Eason张UED-4群🤘")


def test_selected_header_allows_one_ocr_substitution_only_for_long_title():
    target = "米游涩泛二次元同好摸鱼群2.3"

    assert _selected_header_matches("米游涩泛一次元同好摸鱼群2．3（422）", target)
    assert _selected_header_matches("米 游 涩 泛 二 次 元 同 好 摸 鱼 群 23（422）", target)
    assert _selected_header_matches("Eason 张 lJED-4ä#", "Eason张UED-4群🤘")
    assert not _selected_header_matches("米游涩泛二次元同好摸鱼群3.2（422）", target)
    assert not _selected_header_matches("米游涩泛二次元同好摸鱼群32（422）", target)
    assert not _selected_header_matches("Grok张UED-4ä#", "Eason张UED-4群🤘")
    assert not _selected_header_matches("测试一（10）", "测试二")


def test_search_selects_only_group_section_match():
    target = "米游涩泛二次元同好摸鱼群2.3"
    lines = [
        OcrLine(target, 160, 5, 250, 24),
        OcrLine("搜索网络结果", 140, 56, 150, 18),
        OcrLine(target, 140, 104, 270, 20),
        OcrLine("群聊", 140, 210, 50, 18),
        OcrLine("的 米游涩泛二次元同好换角群2．3", 203, 225, 315, 21),
        OcrLine("聊天记录", 137, 300, 75, 18),
        OcrLine(target, 203, 486, 280, 21),
    ]

    matched, detail = _select_group_search_match(lines, target)

    assert detail == ""
    assert matched is lines[4]


def test_search_rejects_similar_group_with_different_version_suffix():
    lines = [
        OcrLine("搜索网络结果", 140, 56, 150, 18),
        OcrLine("群聊", 140, 210, 50, 18),
        OcrLine("米游涩泛二次元同好摸鱼群3.2", 203, 225, 280, 21),
        OcrLine("聊天记录", 137, 300, 75, 18),
    ]

    matched, _ = _select_group_search_match(lines, "米游涩泛二次元同好摸鱼群2.3")

    assert matched is None


def test_search_allows_missing_second_version_digit_but_not_wrong_first_digit():
    target = "米游涩泛二次元同好摸鱼群1.1"
    partial = OcrLine("米游涩泛二次元同好摸鱼群1。", 203, 125, 277, 21)
    wrong = OcrLine("米游涩泛二次元同好摸鱼群2。", 203, 125, 277, 21)
    boundary = OcrLine("聊天记录", 137, 200, 72, 17)

    matched, detail = _select_group_search_match([partial, boundary], target)
    rejected, _ = _select_group_search_match([wrong, boundary], target)

    assert detail == ""
    assert matched is partial
    assert rejected is None


def test_search_accepts_collapsed_version_digits_with_leading_ocr_noise():
    target = "米游涩泛二次元同好摸鱼群2.3"
    noisy_group_result = OcrLine("孙睿 米游涩泛二次元同好摸鱼群23", 10, 95, 320, 24)
    boundary = OcrLine("聊天记录", 10, 150, 90, 24)

    matched, detail = _select_group_search_match([noisy_group_result, boundary], target)

    assert matched == noisy_group_result
    assert detail == ""


def test_search_rejects_different_collapsed_version_digits():
    target = "米游涩泛二次元同好摸鱼群2.3"
    wrong_group_result = OcrLine("孙睿 米游涩泛二次元同好摸鱼群32", 10, 95, 320, 24)
    boundary = OcrLine("聊天记录", 10, 150, 90, 24)

    matched, detail = _select_group_search_match([wrong_group_result, boundary], target)

    assert matched is None
    assert "匹配数 0" in detail


def test_search_does_not_fallback_to_chat_record_when_group_section_exists():
    target = "米游涩泛二次元同好摸鱼群2.3"
    boundary = OcrLine("聊天记录", 10, 150, 90, 24)
    chat_record = OcrLine(target, 10, 210, 320, 24)
    network = OcrLine("搜索网络结果", 10, 300, 150, 24)

    matched, detail = _select_group_search_match([boundary, chat_record, network], target)

    assert matched is None
    assert "匹配数 0" in detail


def test_search_allows_bounded_ocr_errors_with_stable_ascii_anchor():
    lines = [
        OcrLine("Grok App 交 氵 充 君 丰", 204, 125, 164, 23),
        OcrLine("聊天记录", 137, 200, 72, 17),
    ]

    matched, detail = _select_group_search_match(lines, "Grok App 交流群")

    assert detail == ""
    assert matched is lines[0]


def test_search_selects_exact_recent_group_before_network_section():
    lines = [
        OcrLine("Q 茶馆 V4.0（四周年纪念）", 131, 8, 251, 19),
        OcrLine("最常使用", 137, 56, 71, 17),
        OcrLine("茶馆 V4℃（四周年纪念〕可 0", 174, 125, 316, 21),
        OcrLine("搜索网络结果", 138, 199, 146, 18),
        OcrLine("茶馆 V4.0（四周年纪念〕 0", 170, 248, 246, 18),
    ]

    matched, detail = _select_group_search_match(lines, "茶馆V4.0（四周年纪念）🐮🐴")

    assert detail == ""
    assert matched is lines[2]


def test_search_fails_closed_without_chat_history_boundary():
    matched, detail = _select_group_search_match(
        [OcrLine("目标群", 140, 104, 120, 20)],
        "目标群",
    )

    assert matched is None
    assert "匹配数 0" in detail


def test_main_chat_bounds_avoid_optional_right_panel():
    assert _main_chat_horizontal_bounds(0, 1557) == (430, 1557)
    assert _main_chat_horizontal_bounds(0, 2223) == (555, 1400)


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
    clicks: list[tuple[float, float]] = []
    screenshots = iter(
        [
            [OcrLine("其他会话", 10, 10, 100, 20)],
            [
                OcrLine("搜索网络结果", 10, 5, 100, 20),
                OcrLine("文件传输助手", 10, 50, 100, 20),
                OcrLine("群聊", 10, 120, 50, 20),
                OcrLine("文件传输助手", 80, 150, 100, 20),
                OcrLine("聊天记录", 10, 210, 80, 20),
                OcrLine("文件传输助手", 80, 260, 100, 20),
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
    monkeypatch.setattr(driver, "_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, _ = driver.open_and_verify("文件传输助手")

    assert ok is True
    assert hotkeys == [("ctrl", "a"), ("ctrl", "v")]
    assert clicks[0] == (210.0, 49.6)
    assert clicks[1] == (130.0, 200.0)


def test_target_search_retries_transient_ocr_miss(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    search_miss = [OcrLine("搜索网络结果", 10, 5, 100, 20)]
    search_ready = [
        OcrLine("搜索网络结果", 10, 5, 100, 20),
        OcrLine("目标群", 10, 50, 100, 20),
        OcrLine("群聊", 10, 120, 50, 20),
        OcrLine("目标群", 80, 150, 100, 20),
        OcrLine("聊天记录", 10, 210, 80, 20),
    ]
    screenshots = iter(
        [
            [OcrLine("其他会话", 10, 10, 100, 20)],
            search_miss,
            search_ready,
            [OcrLine("目标群", 10, 10, 100, 20)],
        ]
    )
    monkeypatch.setattr(driver, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: True)
    monkeypatch.setattr(driver, "_hotkey", lambda modifier, key: None)
    monkeypatch.setattr(driver, "_key", lambda key, key_up=False: None)
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: next(screenshots))
    monkeypatch.setattr(driver, "_click", lambda x, y: None)
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, _ = driver.open_and_verify("目标群")

    assert ok is True


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
