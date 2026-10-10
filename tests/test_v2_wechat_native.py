from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

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
    _SendLayoutTransition,
)

import pytest


@pytest.fixture(autouse=True)
def isolate_account_guard(monkeypatch, request):
    """这些测试覆盖原有搜索/提交行为；头像安全规则由独立测试覆盖。"""
    if request.node.name.startswith("test_prepare_window"):
        return
    def prepared(driver):
        driver._window = 123
        return True, "mock account verified"
    monkeypatch.setattr(WindowsWechatDriver, "_prepare_wechat_window", prepared)
    monkeypatch.setattr(WindowsWechatDriver, "_assert_send_context", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.sender.wechat_account.inspect_account", lambda *args, **kwargs: {"ok": True, "detail": "mock verified"})


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


def test_prepare_window_uses_only_avatar_verified_identity(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    monkeypatch.setattr("app.sender.wechat_account.inspect_account", lambda *args, **kwargs: {
        "ok": True, "detail": "已核验头像", "identity": {"hwnd": 321, "pid": 999}, "avatar_sha256": "abc"})

    ok, detail = driver._prepare_wechat_window()

    assert ok is True
    assert "已核验" in detail
    assert driver._window == 321


def test_prepare_window_rejects_ambiguous_avatar(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    activated: list[int] = []
    monkeypatch.setattr("app.sender.wechat_account.inspect_account", lambda *args, **kwargs: {"ok": False, "detail": "匹配 2 个头像"})
    monkeypatch.setattr(driver, "_activate", lambda hwnd: activated.append(hwnd) or True)

    ok, detail = driver._prepare_wechat_window()

    assert ok is False
    assert "匹配 2" in detail
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


def _install_search_controls(monkeypatch, controls):
    def descendants(*, control_type):
        assert control_type == "Edit"
        return controls

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(
        Desktop=lambda **kwargs: SimpleNamespace(
            window=lambda **kwargs: SimpleNamespace(descendants=descendants)
        )
    ))


def _search_control(*, aid="", visible=True, readonly=False, accepts=True):
    writes = []
    value = SimpleNamespace(CurrentValue="旧查询", CurrentIsReadOnly=readonly)

    def set_value(query):
        writes.append(query)
        if accepts:
            value.CurrentValue = query

    value.SetValue = set_value
    control = SimpleNamespace(
        element_info=SimpleNamespace(automation_id=aid, name="搜索"),
        is_visible=lambda: visible,
        window_text=lambda: value.CurrentValue,
        iface_value=value,
    )
    return control, writes


def test_search_query_overwrites_only_unique_visible_search_control(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    search, queries = _search_control()
    composer, drafts = _search_control(aid="chat_input_field")
    hidden, hidden_queries = _search_control(visible=False)
    _install_search_controls(monkeypatch, [composer, search, hidden])

    ok, _ = driver._set_search_query("Eason张UED-4群🤘")

    assert ok
    assert queries == ["Eason张UED-4群🤘"]
    assert search.iface_value.CurrentValue == "Eason张UED-4群🤘"
    assert drafts == hidden_queries == []
    assert composer.iface_value.CurrentValue == "旧查询"


@pytest.mark.parametrize("count,readonly,accepts", [
    (0, False, True), (2, False, True), (1, True, True), (1, False, False),
])
def test_search_query_failure_does_not_touch_chat_draft(tmp_path, monkeypatch, count, readonly, accepts):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    searches = [_search_control(readonly=readonly, accepts=accepts) for _ in range(count)]
    composer, drafts = _search_control(aid="chat_input_field")
    _install_search_controls(monkeypatch, [composer] + [control for control, _ in searches])

    ok, detail = driver._set_search_query("目标群")

    assert not ok and detail
    assert drafts == []
    if count != 1 or readonly:
        assert all(not writes for _, writes in searches)


def test_render_only_search_uses_verified_keyboard_fallback(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    _install_search_controls(monkeypatch, [])
    targets = []
    monkeypatch.setattr(driver, "_set_keyboard_search_query", lambda target: (targets.append(target) is None, "verified"))

    assert driver._set_search_query("目标群") == (True, "verified")
    assert targets == ["目标群"]


@pytest.mark.parametrize("caret,owner,foreground,expected", [
    ((175, 84, 177, 86), 123, 123, True),
    ((700, 1100, 702, 1102), 123, 123, False),  # 聊天草稿区
    ((40, 84, 42, 86), 123, 123, False),  # 左侧导航区
    ((175, 84, 175, 86), 123, 123, False),
    ((175, 84, 177, 86), 456, 123, False),
    ((175, 84, 177, 86), 123, 456, False),
])
def test_search_focus_requires_own_foreground_caret_inside_search_area(tmp_path, monkeypatch, caret, owner, foreground, expected):
    import ctypes

    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123

    def get_info(thread, pointer):
        info = pointer._obj
        info.hwndFocus = info.hwndCaret = owner
        info.rcCaret.left, info.rcCaret.top, info.rcCaret.right, info.rcCaret.bottom = caret
        return 1

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(GetGUIThreadInfo=get_info)), raising=False)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetForegroundWindow=lambda: foreground, GetAncestor=lambda h, flag: h,
        ClientToScreen=lambda h, point: (point[0] + 100, point[1] + 200),
    ))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 2)))
    monkeypatch.setattr(driver, "_window_rect", lambda h: (100, 200, 1764, 1603))

    assert driver._search_focus_verified() is expected


def _install_keyboard_search(monkeypatch, driver, *, focus=True, copied="目标群"):
    clipboard = {"text": "original"}
    calls = []

    def hotkey(modifier, key):
        calls.append(key)
        if key == "c" and copied is not None:
            clipboard["text"] = copied

    monkeypatch.setattr(driver, "_hotkey", hotkey)
    monkeypatch.setattr(driver, "_search_focus_verified", lambda: focus)
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: clipboard.update(text=text))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)
    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        OpenClipboard=lambda: None, CloseClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda fmt: True,
        GetClipboardData=lambda fmt: clipboard["text"],
    ))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(CF_UNICODETEXT=13))
    return clipboard, calls


def test_keyboard_search_requires_focus_before_touching_clipboard(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    clipboard, calls = _install_keyboard_search(monkeypatch, driver, focus=False)

    ok, detail = driver._set_keyboard_search_query("目标群")

    assert not ok and "光标" in detail
    assert clipboard["text"] == "original"
    assert calls == ["f"]


def test_keyboard_search_stops_when_focus_changes_before_paste(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    _, calls = _install_keyboard_search(monkeypatch, driver)
    focus = iter([True, True, False])
    monkeypatch.setattr(driver, "_search_focus_verified", lambda: next(focus))

    ok, detail = driver._set_keyboard_search_query("目标群")

    assert not ok and "粘贴前" in detail
    assert calls == ["f", "a"]


@pytest.mark.parametrize("copied,expected", [("目标群", True), ("别的群", False), (None, False)])
def test_keyboard_search_requires_independent_exact_query_readback(tmp_path, monkeypatch, copied, expected):
    driver = WindowsWechatDriver(_settings(tmp_path))
    clipboard, calls = _install_keyboard_search(monkeypatch, driver, copied=copied)

    ok, _ = driver._set_keyboard_search_query("目标群")

    assert ok is expected
    assert calls == ["f", "a", "v", "a", "c"]
    if copied is None:
        assert clipboard["text"].startswith("GroupBrief-search-check-")


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("hit,allowed", [(123, True), (333, True), (444, False), (555, False)])
def test_click_restores_only_own_visible_render_style_even_after_failure(tmp_path, monkeypatch, fails, hit, allowed):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    styles = {1: 0x80020, 2: 0x80020, 3: 0x80020, 4: 0x80020}
    changes = []
    clicked = []

    def set_style(hwnd, index, style):
        styles[hwnd] = style
        changes.append((hwnd, style))

    def mouse_event(*args):
        assert styles[1] == 0x80000
        assert all(styles[h] == 0x80020 for h in (2, 3, 4))
        if fails:
            raise RuntimeError("input failed")
        clicked.append(args)

    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetForegroundWindow=lambda: 123,
        WindowFromPoint=lambda point: hit, GetAncestor=lambda h, flag: h,
        EnumChildWindows=lambda hwnd, callback, out: [callback(h, out) for h in styles],
        GetClassName=lambda h: "Qt51514QWindowToolSaveBits" if h in (333, 444) else "OtherWindow" if h in (2, 555) else "MMUIRenderSubWindowHW",
        GetWindowRect=lambda h: (0, 0, 100, 100) if h != 3 else (200, 200, 300, 300),
        IsWindowVisible=lambda h: h != 4,
        GetWindowLong=lambda h, index: styles[h], SetWindowLong=set_style,
    ))
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        GWL_EXSTYLE=-20, WS_EX_TRANSPARENT=32, MOUSEEVENTF_LEFTDOWN=2, MOUSEEVENTF_LEFTUP=4,
    ))
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(SetCursorPos=lambda point: None, mouse_event=mouse_event))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda h: (1, 99 if h == 444 else 42)))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    if not allowed:
        with pytest.raises(RuntimeError, match="点击位置"):
            driver._click(50, 50)
        assert changes == clicked == []
        return
    if fails:
        with pytest.raises(RuntimeError, match="input failed"):
            driver._click(50, 50)
    else:
        driver._click(50, 50)
        assert len(clicked) == 2
    assert changes == [(1, 0x80000), (1, 0x80020)]
    assert all(style == 0x80020 for style in styles.values())


def test_failed_search_control_stops_before_clicking_or_typing(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    monkeypatch.setattr(driver, "_imports", lambda: None)
    monkeypatch.setattr(driver, "_desktop_unlocked", lambda: True)
    monkeypatch.setattr(driver, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: True)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_set_search_query", lambda target: (False, "搜索控件不可用"))

    def forbidden(*args):
        pytest.fail("Failed search must not click, overwrite clipboard, or type into a draft")

    for method in ("_click", "_hotkey", "_set_clipboard_text", "_ocr_screen"):
        monkeypatch.setattr(driver, method, forbidden)

    ok, detail = driver.open_and_verify("目标群")

    assert not ok
    assert "搜索控件不可用" in detail and "已停止发送" in detail


def test_target_search_sets_query_without_composer_hotkeys(tmp_path, monkeypatch):
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
    monkeypatch.setattr(driver, "_set_search_query", lambda target: (True, "query verified"))
    monkeypatch.setattr(driver, "_hotkey", lambda modifier, key: hotkeys.append((modifier, key)))
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: next(screenshots))
    monkeypatch.setattr(driver, "_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, _ = driver.open_and_verify("文件传输助手")

    assert ok is True
    assert hotkeys == []
    assert clicks == [(130.0, 200.0)]


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
    monkeypatch.setattr(driver, "_set_search_query", lambda target: (True, "query verified"))
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
    monkeypatch.setattr(driver, "_set_search_query", lambda target: (True, "query verified"))
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
    assert clicks == [(160.0, 200.0)]


@pytest.mark.parametrize(
    "titles,expected",
    [
        (["UIED.CN🌍探索未来AIGC⑮"], True),
        (["UIED.CN🌍探索未来AIGC⑯"], False),
        (["UIED.CN🌍探索未来AIGC"], False),
        (["UIED.CN探索未来AIGC15"], False),
        (["UIED.CN🌍探索未来AIGC⑮", "UIED.CN🌍探索未来AIGC⑮"], False),
        ([], False),
    ],
)
def test_live_uied_ocr_failure_requires_unique_exact_uia_header(tmp_path, monkeypatch, titles, expected):
    driver = WindowsWechatDriver(_settings(tmp_path))
    target = "UIED.CN🌍探索未来AIGC⑮"
    clicks = []
    monkeypatch.setattr(driver, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(driver, "_imports", lambda: None)
    monkeypatch.setattr(driver, "_desktop_unlocked", lambda: True)
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [123])
    monkeypatch.setattr(driver, "_activate", lambda hwnd: True)
    monkeypatch.setattr(driver, "_set_search_query", lambda target: (True, "query verified"))
    monkeypatch.setattr(driver, "_hotkey", lambda *args: None)
    monkeypatch.setattr(driver, "_set_clipboard_text", lambda text: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    # Actual Windows OCR output: both the search item and title lost AIGC⑮.
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: [
        OcrLine("UIED ℃ N 探 索 未 来 C@（497）", 10, 10, 375, 27)
    ])
    monkeypatch.setattr(driver, "_find_uia_group_search_match", lambda *args: (
        OcrLine(target, 80, 150, 160, 20), ""
    ))
    monkeypatch.setattr(driver, "_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(driver, "_read_uia_chat_titles", lambda box: titles)
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda seconds: None)

    ok, detail = driver.open_and_verify(target)

    assert ok is expected
    assert len(clicks) == 1  # A real group result must be selected before title fallback.
    assert ("UIA 聊天标题" if expected else "已停止发送") in detail


def test_uia_header_reads_only_visible_title_control_inside_current_window(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    title_id = (
        "content_view.top_content_view.title_h_view.left_v_view."
        "left_content_v_view.left_ui_.big_title_line_h_view.current_chat_name_label"
    )

    def control(text, aid=title_id, visible=True, rect=(450, 20, 850, 60)):
        return SimpleNamespace(
            element_info=SimpleNamespace(
                automation_id=aid,
                rectangle=SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]),
            ),
            is_visible=lambda: visible,
            window_text=lambda: text,
        )

    controls = [
        control("UIED.CN🌍探索未来AIGC⑮"),
        control("hidden", visible=False),
        control("outside", rect=(450, 300, 850, 340)),
        control("empty bounds", rect=(450, 20, 450, 60)),
        control("search result", aid="search_item_UIED.CN🌍探索未来AIGC⑮"),
        control("composer", aid="chat_input_field"),
        control("message", aid="chat_message_list.qt_scrollarea_viewport.chat_bubble_item_view"),
    ]

    def descendants(*, control_type):
        assert control_type == "Text"
        return controls

    def window(*, handle):
        assert handle == 123
        return SimpleNamespace(descendants=descendants)

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(
        Desktop=lambda **kwargs: SimpleNamespace(window=window)
    ))
    assert driver._read_uia_chat_titles((430, 0, 1000, 128)) == ["UIED.CN🌍探索未来AIGC⑮"]
    controls.append(control("UIED.CN🌍探索未来AIGC⑮"))
    assert len(driver._read_uia_chat_titles((430, 0, 1000, 128))) == 2
    driver._window = None
    assert driver._read_uia_chat_titles((430, 0, 1000, 128)) == []


def test_uia_header_failure_is_closed(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123

    def unavailable(**kwargs):
        raise RuntimeError("UIA unavailable")

    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=unavailable))
    assert driver._read_uia_chat_titles((430, 0, 1000, 128)) == []


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


def _install_send_regions(monkeypatch, composer, chat, *, extra=()):
    def control(kind, aid, box, visible=True):
        return SimpleNamespace(
            element_info=SimpleNamespace(
                automation_id=aid,
                rectangle=SimpleNamespace(left=box[0], top=box[1], right=box[2], bottom=box[3]),
            ),
            kind=kind,
            is_visible=lambda: visible,
        )
    controls = [control("Edit", "chat_input_field", composer), control("List", "chat_message_list", chat)]
    controls += [control(*args) for args in extra]
    seen_handles = []
    def window(*, handle):
        seen_handles.append(handle)
        return SimpleNamespace(descendants=lambda *, control_type: [c for c in controls if c.kind == control_type])
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **kwargs: SimpleNamespace(window=window)))
    return controls, seen_handles


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 1.75, 2])
@pytest.mark.parametrize("origin", [(0, 0), (-1920, 400), (800, -1440)])
def test_send_regions_use_actual_controls_at_any_scale_and_monitor(tmp_path, monkeypatch, scale, origin):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    def box(value):
        return tuple(int(v * scale) + origin[i % 2] for i, v in enumerate(value))
    composer, chat = box((360, 710, 1090, 880)), box((350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: box((0, 0, 1100, 950)))
    controls, handles = _install_send_regions(monkeypatch, composer, chat, extra=[
        ("Edit", "search_input", box((80, 50, 280, 90)), True),
        ("Edit", "chat_input_field", composer, False),
    ])
    grabbed, clicks = [], []
    def grab(*, bbox, all_screens):
        assert all_screens
        grabbed.append(bbox)
        return Image.new("RGB", (bbox[2] - bbox[0], bbox[3] - bbox[1]), "white")
    monkeypatch.setattr("PIL.ImageGrab.grab", grab)
    monkeypatch.setattr(driver, "_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda *args: None)
    driver._focus_composer()
    driver._capture_send_regions()
    assert grabbed == [composer, chat]
    assert handles == [123, 123]
    assert clicks == [((composer[0] + composer[2]) / 2, (composer[1] + composer[3]) / 2)]


@pytest.mark.parametrize("composer,chat,extra", [
    ((350, 710, 1090, 880), (350, 140, 1100, 700), [("Edit", "chat_input_field", (350, 710, 1090, 880), True)]),
    ((350, 710, 1200, 880), (350, 140, 1100, 700), []),
    ((350, 690, 1090, 880), (350, 140, 1100, 700), []),
    ((350, 710, 1090, 720), (350, 140, 1100, 700), []),
])
def test_invalid_send_regions_stop_without_capture(tmp_path, monkeypatch, composer, chat, extra):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    _install_send_regions(monkeypatch, composer, chat, extra=extra)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: pytest.fail("must not capture guessed regions"))
    with pytest.raises(RuntimeError):
        driver._capture_send_regions()


def test_send_region_layout_change_is_rejected(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: boxes)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: Image.new("RGB", (200, 80), "white"))
    driver._capture_send_regions()
    boxes = ((400, 710, 1140, 880), (400, 140, 1150, 700))
    with pytest.raises(RuntimeError, match="布局或窗口位置已变化"):
        driver._capture_send_regions()


def test_actual_composer_crop_ignores_unrelated_session_changes(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: boxes)
    screen = Image.new("RGB", (1100, 950), "white")
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda *, bbox, all_screens: screen.crop(bbox))
    before, chat_before = driver._capture_send_regions()
    ImageDraw.Draw(screen).rectangle((370, 740, 850, 850), fill="black")
    staged, _ = driver._capture_send_regions()
    ImageDraw.Draw(screen).rectangle(boxes[0], fill="white")
    # 会话红点、发送按钮与聊天消息都可变化，但不能算成编辑框残留。
    ImageDraw.Draw(screen).rectangle((100, 720, 340, 870), fill="green")
    ImageDraw.Draw(screen).rectangle((980, 900, 1090, 930), fill="grey")
    ImageDraw.Draw(screen).rectangle((400, 600, 850, 690), fill="black")
    after, chat_after = driver._capture_send_regions()
    assert driver._verify_submission(before, staged, after, chat_before, chat_after)[0]
    # 消息区变化仍不足以证明发送：尚有文字/图片预览时必须失败。
    ImageDraw.Draw(screen).rectangle((370, 740, 850, 850), fill="black")
    still_staged, _ = driver._capture_send_regions()
    assert not driver._verify_submission(before, staged, still_staged, chat_before, chat_after)[0]
    assert not driver._verify_submission(before, staged, after, chat_before, chat_before)[0]


@pytest.mark.parametrize("width,height", [(2, 16), (4, 32), (6, 48)])
def test_empty_green_caret_blink_is_not_staging_or_residual_text(width, height):
    before = Image.new("RGB", (800, 170), (250, 250, 250))
    caret = before.copy()
    ImageDraw.Draw(caret).rectangle((0, 5, width - 1, 5 + height - 1), fill=(0, 195, 117))
    assert WindowsWechatDriver._composer_difference_ratio(before, caret) == 0
    assert WindowsWechatDriver._composer_difference_ratio(caret, before) == 0
    staged = before.copy()
    ImageDraw.Draw(staged).rectangle((15, 10, 500, 120), fill="black")
    chat = before.copy()
    ImageDraw.Draw(chat).rectangle((15, 10, 500, 120), fill="black")
    assert WindowsWechatDriver._verify_submission(before, staged, caret, before, chat)[0]
    assert not WindowsWechatDriver._verify_submission(before, caret, before, before, chat)[0]


@pytest.mark.parametrize("box,color", [
    ((0, 5, 3, 36), "black"),
    ((20, 5, 23, 36), (0, 195, 117)),
    ((0, 5, 20, 36), (0, 195, 117)),
    ((0, 5, 3, 90), (0, 195, 117)),
])
def test_composer_caret_filter_keeps_text_and_preview_changes(box, color):
    before = Image.new("RGB", (800, 170), (250, 250, 250))
    after = before.copy()
    ImageDraw.Draw(after).rectangle(box, fill=color)
    assert WindowsWechatDriver._composer_difference_ratio(before, after) > 0


@pytest.mark.parametrize("stage", ["text", "image"])
def test_expanded_composer_is_verified_without_resizing_its_content(stage):
    empty = Image.new("RGB", (800, 170), (250, 250, 250))
    expanded = Image.new("RGB", (800, 360), (250, 250, 250))
    ImageDraw.Draw(expanded).rectangle((20, 10, 200, 310), fill="black" if stage == "text" else "blue")
    chat_before = Image.new("RGB", (800, 700), "white")
    chat_after = chat_before.copy()
    ImageDraw.Draw(chat_after).rectangle((20, 450, 200, 650), fill="blue")
    assert WindowsWechatDriver._verify_submission(empty, expanded, empty, chat_before, chat_after)[0]
    assert not WindowsWechatDriver._verify_submission(empty, expanded, expanded, chat_before, chat_after)[0]
    assert not WindowsWechatDriver._verify_submission(empty, expanded, empty, chat_before, chat_before)[0]
    assert WindowsWechatDriver._composer_difference_ratio(empty, Image.new("RGB", (800, 360), (250, 250, 250))) == 0


def test_chat_height_change_alone_does_not_count_as_message_submission():
    small = Image.new("RGB", (800, 400), "white")
    large = Image.new("RGB", (800, 700), "white")
    assert WindowsWechatDriver._chat_difference_ratio(small, large) == 0
    assert WindowsWechatDriver._chat_difference_ratio(large, small) == 0


def test_capture_accepts_editor_expansion_and_collapse_with_stable_window(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    window_box = (0, 0, 1100, 950)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: window_box)
    boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: boxes)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda *, bbox, all_screens: Image.new("RGB", (bbox[2]-bbox[0], bbox[3]-bbox[1]), "white"))
    before, _ = driver._capture_send_regions()
    boxes = ((350, 510, 1090, 880), (350, 140, 1100, 500))
    staged, _ = driver._capture_send_regions()
    assert staged.height == 370
    boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    after, _ = driver._capture_send_regions()
    assert before.size == after.size
    window_box = (0, 0, 1101, 950)
    with pytest.raises(RuntimeError, match="布局或窗口位置已变化"):
        driver._capture_send_regions()


@pytest.mark.parametrize("boxes", [
    ((350, 510, 1090, 880), (350, 140, 1100, 700)),
    ((350, 510, 1090, 880), (350, 180, 1100, 500)),
    ((350, 730, 1090, 880), (350, 140, 1100, 720)),
])
def test_unrelated_layout_change_is_not_editor_expansion(tmp_path, monkeypatch, boxes):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda *args: None)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    baseline = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: baseline)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: Image.new("RGB", (200, 80), "white"))
    driver._capture_send_regions()
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: boxes)
    with pytest.raises(RuntimeError, match="布局或窗口位置已变化|展开/收起未完成"):
        driver._capture_send_regions()


@pytest.mark.parametrize("editor_first", [False, True])
def test_capture_waits_for_both_sides_of_expansion_animation(tmp_path, monkeypatch, editor_first):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda *args: None)
    baseline = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    expanded = ((350, 510, 1090, 880), (350, 140, 1100, 500))
    partial = ((350, 510, 1090, 880), (350, 140, 1100, 650)) if editor_first else ((350, 650, 1090, 880), (350, 140, 1100, 500))
    pending = iter([baseline, partial, expanded])
    reads, captured = [], []
    def boxes():
        value = next(pending)
        reads.append(value)
        if value == partial and editor_first:
            raise _SendLayoutTransition(value)
        return value
    def grab(*, bbox, all_screens):
        captured.append(bbox)
        return Image.new("RGB", (bbox[2]-bbox[0], bbox[3]-bbox[1]), "white")
    monkeypatch.setattr(driver, "_read_send_region_boxes", boxes)
    monkeypatch.setattr("PIL.ImageGrab.grab", grab)
    driver._capture_send_regions()
    driver._capture_send_regions()
    assert reads == [baseline, partial, expanded]
    assert captured == [*baseline, *expanded]


def test_expansion_that_never_finishes_is_not_captured(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    driver._send_window_box = (0, 0, 1100, 950)
    driver._send_region_boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: driver._send_window_box)
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: ((350, 650, 1090, 880), (350, 140, 1100, 500)))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda *args: None)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: pytest.fail("unstable layout must not be captured"))
    with pytest.raises(RuntimeError, match="展开/收起未完成"):
        driver._capture_send_regions()


def test_image_waits_for_expansion_then_verifies_collapse_after_enter(tmp_path, monkeypatch):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1100, 950))
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda *args: None)
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_set_clipboard_image", lambda *args: None)
    state = {"phase": "empty", "partial_seen": False}
    normal = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    partial = ((350, 650, 1090, 880), (350, 140, 1100, 500))
    expanded = ((350, 510, 1090, 880), (350, 140, 1100, 500))
    def boxes():
        if state["phase"] == "animating":
            state["partial_seen"] = True
            state["phase"] = "staged"
            return partial
        return expanded if state["phase"] == "staged" else normal
    def grab(*, bbox, all_screens):
        image = Image.new("RGB", (bbox[2]-bbox[0], bbox[3]-bbox[1]), (250, 250, 250))
        if bbox == expanded[0] and state["phase"] == "staged":
            ImageDraw.Draw(image).rectangle((20, 10, 200, 310), fill="blue")
        if bbox == normal[1] and state["phase"] == "sent":
            ImageDraw.Draw(image).rectangle((20, 400, 200, 550), fill="blue")
        return image
    def enter(name):
        assert name == "enter" and state["phase"] == "staged" and state["partial_seen"]
        state["phase"] = "sent"
    monkeypatch.setattr(driver, "_read_send_region_boxes", boxes)
    monkeypatch.setattr("PIL.ImageGrab.grab", grab)
    monkeypatch.setattr(driver, "_hotkey", lambda *args: state.update(phase="animating"))
    monkeypatch.setattr(driver, "_key", enter)
    result = driver.paste_image(tmp_path / "image.png")
    assert result.success and result.submitted and not result.outcome_unknown
    assert result.verification_level == "ui_observed"


@pytest.mark.parametrize("bottom_delta", [-2, -1, 1, 2])
def test_editor_bottom_rounding_is_bounded_to_one_pixel(tmp_path, monkeypatch, bottom_delta):
    driver = WindowsWechatDriver(_settings(tmp_path))
    driver._window = 123
    window = (0, 0, 1100, 950)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: window)
    boxes = ((350, 710, 1090, 880), (350, 140, 1100, 700))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: boxes)
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **kwargs: Image.new("RGB", (200, 80), "white"))
    driver._capture_send_regions()
    boxes = ((350, 510, 1090, 880 + bottom_delta), (350, 140, 1100, 500))
    if abs(bottom_delta) > 1:
        with pytest.raises(RuntimeError, match="布局或窗口位置已变化"):
            driver._capture_send_regions()
    else:
        driver._capture_send_regions()
        window = (0, 1, 1100, 951)
        with pytest.raises(RuntimeError, match="布局或窗口位置已变化"):
            driver._capture_send_regions()
