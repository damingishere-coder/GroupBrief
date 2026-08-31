from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.config.settings import Settings
from app.sender.wechat_native import (
    NativeActionResult,
    OcrLine,
    UiaSearchItem,
    WechatNativeSender,
    WindowsWechatDriver,
    create_wechat_sender,
    _main_chat_horizontal_bounds,
    _selected_header_matches,
    _select_group_search_match,
    _select_uia_group_search_match,
    _title_matches,
)

import pytest


def _write_valid_png(path: Path) -> None:
    Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(path, format="PNG")


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


def test_sender_factory_rejects_unknown_mode(tmp_path):
    settings = _settings(tmp_path)
    settings.wechat_sender_mode = "typo_sender"
    with pytest.raises(ValueError, match="不支持的微信发送 Provider"):
        create_wechat_sender(settings=settings, dry_run=True)


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
    assert not _selected_header_matches("Grok Web 交流群", "Grok App 交流群")


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
    section = OcrLine("群聊", 137, 70, 50, 18)
    partial = OcrLine("米游涩泛二次元同好摸鱼群1。", 203, 125, 277, 21)
    wrong = OcrLine("米游涩泛二次元同好摸鱼群2。", 203, 125, 277, 21)
    boundary = OcrLine("聊天记录", 137, 200, 72, 17)

    matched, detail = _select_group_search_match([section, partial, boundary], target)
    rejected, _ = _select_group_search_match([section, wrong, boundary], target)

    assert detail == ""
    assert matched is partial
    assert rejected is None


def test_search_accepts_collapsed_version_digits_with_leading_ocr_noise():
    target = "米游涩泛二次元同好摸鱼群2.3"
    section = OcrLine("群聊", 10, 50, 60, 24)
    noisy_group_result = OcrLine("孙睿 米游涩泛二次元同好摸鱼群23", 10, 95, 320, 24)
    boundary = OcrLine("聊天记录", 10, 150, 90, 24)

    matched, detail = _select_group_search_match([section, noisy_group_result, boundary], target)

    assert matched == noisy_group_result
    assert detail == ""


def test_search_rejects_different_collapsed_version_digits():
    target = "米游涩泛二次元同好摸鱼群2.3"
    section = OcrLine("群聊", 10, 50, 60, 24)
    wrong_group_result = OcrLine("孙睿 米游涩泛二次元同好摸鱼群32", 10, 95, 320, 24)
    boundary = OcrLine("聊天记录", 10, 150, 90, 24)

    matched, detail = _select_group_search_match([section, wrong_group_result, boundary], target)

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
        OcrLine("最常使用", 137, 56, 71, 17),
        OcrLine("Grok App 交 氵 充 君 丰", 204, 125, 164, 23),
        OcrLine("聊天记录", 137, 200, 72, 17),
    ]

    matched, detail = _select_group_search_match(lines, "Grok App 交流群")

    assert detail == ""
    assert matched is lines[1]


def test_search_selects_grok_only_from_real_trusted_section_layout():
    target = "Grok App 交流群"
    trusted_result = OcrLine("Grok App 交 氵 充 君 丰", 202, 124, 190, 24)
    lines = [
        OcrLine(target, 130, 8, 180, 20),
        OcrLine("最常使用", 137, 56, 71, 17),
        trusted_result,
        OcrLine("聊天记录", 137, 190, 72, 17),
        OcrLine(target, 202, 230, 190, 24),
        OcrLine("搜索网络结果", 137, 310, 145, 18),
        OcrLine(target, 202, 350, 190, 24),
    ]

    matched, detail = _select_group_search_match(lines, target)

    assert detail == ""
    assert matched is trusted_result


def test_search_tolerates_one_ocr_substitution_in_most_used_section():
    target = "Eason张UED-4群🤘"
    trusted_result = OcrLine("Eason 张 UED-4 君 羊", 239, 158, 192, 24)
    matched, detail = _select_group_search_match(
        [
            OcrLine("最 常 使 岸", 160, 78, 82, 20),
            trusted_result,
            OcrLine("聊 天 记 录", 160, 246, 84, 20),
            OcrLine("Eason 张 UED-4ä*", 162, 308, 269, 24),
            OcrLine("搜 索 网 络 结 果", 162, 574, 169, 21),
        ],
        target,
    )

    assert detail == ""
    assert matched is trusted_result


def test_search_accepts_live_grok_ocr_only_inside_trusted_section():
    target = "Grok App 交流群"
    trusted_result = OcrLine("Gr01< App 交 氵 充 君 羊", 238, 158, 192, 27)
    matched, detail = _select_group_search_match(
        [
            OcrLine("最 常 使 岸", 160, 78, 82, 20),
            trusted_result,
            OcrLine("聊 天 记 录", 160, 246, 84, 20),
            OcrLine("Gr01< App 交 氵 充 君 羊", 238, 308, 192, 27),
        ],
        target,
    )

    assert detail == ""
    assert matched is trusted_result


def test_search_rejects_wrong_english_token_even_in_trusted_section():
    target = "Grok App 交流群"
    matched, detail = _select_group_search_match(
        [
            OcrLine("最常使用", 160, 78, 82, 20),
            OcrLine("Grok Web 交流群", 238, 158, 192, 27),
            OcrLine("聊天记录", 160, 246, 84, 20),
        ],
        target,
    )

    assert matched is None
    assert "匹配数 0" in detail


def test_search_rejects_target_when_trusted_group_section_is_missing():
    target = "Grok App 交流群"
    matched, detail = _select_group_search_match(
        [
            OcrLine(target, 202, 124, 190, 24),
            OcrLine("聊天记录", 137, 190, 72, 17),
            OcrLine(target, 202, 230, 190, 24),
        ],
        target,
    )

    assert matched is None
    assert "可信分区 0" in detail


def test_uia_search_selects_only_unique_exact_group_item_inside_search_box():
    target = "Grok App 交流群"
    search_box = (10, 20, 500, 600)
    exact = UiaSearchItem(target, f"search_item_{target}", 100, 120, 330, 170)
    chat_record = UiaSearchItem(target, "", 100, 220, 330, 270)
    network = UiaSearchItem(target, "network_result", 100, 320, 330, 370)

    matched, detail = _select_uia_group_search_match(
        [chat_record, network, exact],
        target,
        search_box,
    )

    assert detail == ""
    assert matched == OcrLine(target, 90, 100, 230, 50)


def test_uia_search_rejects_duplicate_or_out_of_bounds_exact_items():
    target = "Grok App 交流群"
    expected_id = f"search_item_{target}"
    inside = UiaSearchItem(target, expected_id, 100, 120, 330, 170)
    duplicate = UiaSearchItem(target, expected_id, 100, 180, 330, 230)
    outside = UiaSearchItem(target, expected_id, 600, 120, 830, 170)

    ambiguous, ambiguous_detail = _select_uia_group_search_match(
        [inside, duplicate],
        target,
        (10, 20, 500, 600),
    )
    bounded, bounded_detail = _select_uia_group_search_match(
        [outside],
        target,
        (10, 20, 500, 600),
    )

    assert ambiguous is None
    assert "当前 2" in ambiguous_detail
    assert bounded is None
    assert "当前 0" in bounded_detail


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
    _write_valid_png(image)
    driver = FakeNativeDriver()
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)

    text_result, image_result = sender.send_bundle("文件传输助手", "唯一标记", image)

    assert text_result.success
    assert image_result is not None and image_result.success
    assert [call[0] for call in driver.calls] == ["verify", "text", "image"]


def test_bundle_stops_when_target_verification_fails(tmp_path):
    image = tmp_path / "daily_image.png"
    _write_valid_png(image)
    driver = FakeNativeDriver(verify=False)
    sender = WechatNativeSender(_settings(tmp_path), driver=driver)

    text_result, image_result = sender.send_bundle("重名群", "不会发送", image)

    assert not text_result.success
    assert image_result is None
    assert [call[0] for call in driver.calls] == ["verify"]


def test_bundle_reports_image_failure_after_text_success(tmp_path):
    image = tmp_path / "daily_image.png"
    _write_valid_png(image)
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
            return NativeActionResult(
                False,
                "UI 未确认",
                True,
                "unknown",
                True,
                {"phase": "submit_unknown", "submit_attempts": 3},
            )

    sender = WechatNativeSender(_settings(tmp_path), driver=UnknownDriver())

    result = sender.send_text("文件传输助手", "文字")

    assert result.success is False
    assert result.submitted is True
    assert result.outcome_unknown is True
    assert result.verification_level == "unknown"
    assert result.diagnostics["phase"] == "submit_unknown"


def test_text_waits_for_staged_change_before_enter(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    before = Image.new("RGB", (20, 20), "white")
    staged = Image.new("RGB", (20, 20), "black")
    pressed: list[str] = []
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_capture_stable_baseline", lambda: (True, before, before, 2))
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda _text: None)
    monkeypatch.setattr(driver, "_hotkey", lambda *_args: None)
    monkeypatch.setattr(driver, "_wait_for_staged_change", lambda _before: (staged, 0.1, 4))
    monkeypatch.setattr(driver, "_key", lambda key, key_up=False: pressed.append(key) if not key_up else None)
    monkeypatch.setattr(
        driver,
        "_wait_for_submission",
        lambda *_args: (True, "ok", {"phase": "submit_verified", "submit_attempts": 3}),
    )

    result = driver.paste_text("文字")

    assert result.success is True
    assert pressed == ["enter"]
    assert result.diagnostics["stage_attempts"] == 4
    assert result.diagnostics["submit_attempts"] == 3


def test_text_never_enters_when_staged_change_is_not_observed(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    before = Image.new("RGB", (20, 20), "white")
    pressed: list[str] = []
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_capture_stable_baseline", lambda: (True, before, before, 1))
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda _text: None)
    monkeypatch.setattr(driver, "_hotkey", lambda *_args: None)
    monkeypatch.setattr(driver, "_wait_for_staged_change", lambda _before: (None, 0.0, 25))
    monkeypatch.setattr(driver, "_key", lambda key, key_up=False: pressed.append(key))

    result = driver.paste_text("文字")

    assert result.success is False
    assert result.submitted is False
    assert result.outcome_unknown is False
    assert pressed == []
    assert "未按 Enter" in result.detail


def test_text_holds_without_touching_draft_when_composer_is_not_empty(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    clipboard_writes: list[str] = []
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (False, "检测到草稿"))
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: clipboard_writes.append(text))

    result = driver.paste_text("文字")

    assert result.success is False
    assert result.submitted is False
    assert clipboard_writes == []
    assert "草稿" in result.detail


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


def test_prepare_window_restores_only_one_existing_hidden_main_window(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    visible = iter([[], [321]])
    activated: list[int] = []
    monkeypatch.setattr(driver, "_wechat_windows", lambda: next(visible))
    monkeypatch.setattr(driver, "_hidden_wechat_windows", lambda: [321])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: activated.append(hwnd) or True)

    ok, detail = driver._prepare_wechat_window()

    assert ok is True
    assert "已恢复" in detail
    assert activated == [321]


def test_prepare_window_rejects_multiple_hidden_candidates_without_activation(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    activated: list[int] = []
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [])
    monkeypatch.setattr(driver, "_hidden_wechat_windows", lambda: [321, 654])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: activated.append(hwnd) or True)

    ok, detail = driver._prepare_wechat_window()

    assert ok is False
    assert "隐藏候选 2" in detail
    assert activated == []


def test_open_and_verify_never_restores_window_while_desktop_is_locked(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    monkeypatch.setattr("app.sender.wechat_native.os.name", "nt")
    monkeypatch.setattr(driver, "_desktop_unlocked", lambda: False)
    monkeypatch.setattr(
        driver,
        "_prepare_wechat_window",
        lambda: pytest.fail("桌面锁定时不得尝试恢复微信窗口"),
    )

    ok, detail = driver.open_and_verify("Grok App 交流群")

    assert ok is False
    assert "桌面已锁定" in detail


def test_wechat_main_window_class_rejects_auxiliary_windows():
    assert WindowsWechatDriver._wechat_main_window_class("Qt51514QWindowIcon")
    assert WindowsWechatDriver._wechat_main_window_class("WeChatMainWndForPC")
    assert not WindowsWechatDriver._wechat_main_window_class("Qt51514QWindowToolSaveBits")
    assert not WindowsWechatDriver._wechat_main_window_class("Chrome_WidgetWin_1")


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


def test_target_search_falls_back_to_unique_uia_group_item(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    target = "Grok App 交流群"
    screenshots = iter(
        [
            [OcrLine("其他会话", 10, 10, 100, 20)],
            [OcrLine("聊天记录", 10, 210, 80, 20)],
            [OcrLine("聊天记录", 10, 210, 80, 20)],
            [OcrLine("聊天记录", 10, 210, 80, 20)],
            [OcrLine(target, 10, 10, 160, 20)],
        ]
    )
    clicks: list[tuple[float, float]] = []
    monkeypatch.setattr(driver, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: True)
    monkeypatch.setattr(driver, "_hotkey", lambda modifier, key: None)
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: next(screenshots))
    monkeypatch.setattr(driver, "_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(
        driver,
        "_find_uia_group_search_match",
        lambda value, box: (OcrLine(value, 80, 150, 160, 20), ""),
    )
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, detail = driver.open_and_verify(target)

    assert ok is True
    assert "精确查找并验证" in detail
    assert clicks[1] == (160.0, 200.0)


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
