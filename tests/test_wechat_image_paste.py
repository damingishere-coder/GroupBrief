"""图片粘贴前真实接收控件与剪贴板内容保护；所有桌面/剪贴板调用均隔离。"""
import hashlib
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from app.config.settings import Settings
from app.sender.wechat_native import WindowsWechatDriver


def driver_for(tmp_path, monkeypatch):
    monkeypatch.setattr(WindowsWechatDriver, "_enable_dpi_awareness", lambda *_: None)
    driver = WindowsWechatDriver(Settings(wechat_native_stage_timeout_seconds=0.2, wechat_native_poll_interval_seconds=0.1))
    driver._window = 123
    monkeypatch.setattr(driver, "_assert_send_context", lambda: None)
    monkeypatch.setattr("app.sender.wechat_native.time.sleep", lambda _: None)
    return driver


def install_clipboard(monkeypatch):
    state = {"data": b"", "sequence": 50, "available": True, "events": [],
             "formats": [8, 2, 17], "dirty": False, "read_count": 0}
    def empty():
        state["data"] = b""
        state["formats"] = []
        state["dirty"] = True
        state["events"].append("empty")
    def put(fmt, value):
        assert fmt == 8
        state["data"] = value
        state["formats"] = [8, 2, 17]
        state["dirty"] = True
        state["sequence"] += 1
        state["events"].append("write")
    def close():
        state["events"].append("close")
        if state["dirty"]:
            # 本机真实复现：发布 DIB 时关闭写入锁后序号 +2，字节不变。
            # 后续只读 Open/Close 不能产生第二次发布或改变序号。
            state["sequence"] += 2
            state["dirty"] = False
            hook = state.get("after_write_close")
            if hook:
                hook()
    def get_data(_):
        state["read_count"] += 1
        return state.get("readback_override", state["data"])
    def enum_formats(previous):
        formats = state["formats"]
        index = 0 if previous == 0 else formats.index(previous) + 1
        return formats[index] if index < len(formats) else 0
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(
        CF_DIB=8, CF_BITMAP=2, CF_DIBV5=17, CF_UNICODETEXT=13, CF_HDROP=15,
    ))
    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        OpenClipboard=lambda: state["events"].append("open"),
        CloseClipboard=close,
        EmptyClipboard=empty,
        SetClipboardData=put,
        GetClipboardData=get_data,
        IsClipboardFormatAvailable=lambda _: state["available"],
        GetClipboardSequenceNumber=lambda: state["sequence"],
        EnumClipboardFormats=enum_formats,
    ))
    return state


def test_image_clipboard_is_read_back_and_verified_without_recopy(tmp_path, monkeypatch):
    state = install_clipboard(monkeypatch)
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 128), "blue").save(image)
    token = WindowsWechatDriver._set_clipboard_image(image)
    assert token["sequence"] == state["sequence"] == 53
    assert set(token["formats"]) == {8, 2, 17}
    assert token["dib_sha256"] == hashlib.sha256(state["data"]).hexdigest()
    assert state["read_count"] == 2
    result = WindowsWechatDriver._verify_image_clipboard(token)
    assert result["clipboard_verified"]
    assert result["clipboard_sequence"] == 53
    assert set(result["clipboard_formats"]) == {8, 2, 17}
    assert result["clipboard_dib_size_bytes"] > 64 * 128 * 3
    assert state["events"] == ["open", "empty", "write", "close", "open", "close", "open", "close"]
    assert state["read_count"] == 3 and state["sequence"] == 53


@pytest.mark.parametrize("change,error", [("sequence", "已被更换"), ("format", "不再包含图片"), ("content", "内容已变化")])
def test_image_clipboard_changes_stop_without_clearing_or_recopying(monkeypatch, change, error):
    state = install_clipboard(monkeypatch)
    state["data"] = b"original"
    token = {"sequence": 50, "dib_sha256": hashlib.sha256(b"original").hexdigest(),
             "dib_size_bytes": len(b"original"), "formats": [8, 2, 17]}
    if change == "sequence":
        state["sequence"] = 51
    elif change == "format":
        state["available"] = False
    else:
        state["data"] = b"different"
    with pytest.raises(RuntimeError, match=error):
        WindowsWechatDriver._verify_image_clipboard(token)
    assert state["events"] == ["open", "close"]


@pytest.mark.parametrize("extra_format", [13, 15, 49161])
def test_image_clipboard_rejects_non_image_formats_even_with_same_sequence_and_dib(monkeypatch, extra_format):
    state = install_clipboard(monkeypatch)
    state["data"] = b"original"
    token = {"sequence": 50, "dib_sha256": hashlib.sha256(b"original").hexdigest(),
             "dib_size_bytes": len(b"original"), "formats": [8, 2, 17]}
    state["formats"].append(extra_format)
    with pytest.raises(RuntimeError, match="格式"):
        WindowsWechatDriver._verify_image_clipboard(token)
    assert state["events"] == ["open", "close"]


@pytest.mark.parametrize("change", ["content", "format"])
def test_image_clipboard_rechecks_published_content_without_rewriting(tmp_path, monkeypatch, change):
    state = install_clipboard(monkeypatch)
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 128), "blue").save(image)
    def change_after_close():
        if change == "content":
            state["data"] = b"replacement"
        else:
            state["formats"].append(13)
    state["after_write_close"] = change_after_close
    with pytest.raises(RuntimeError):
        WindowsWechatDriver._set_clipboard_image(image)
    assert state["events"] == ["open", "empty", "write", "close", "open", "close"]
    assert state["events"].count("write") == 1


def test_image_clipboard_first_readback_failure_still_closes_without_recopy(tmp_path, monkeypatch):
    state = install_clipboard(monkeypatch)
    state["readback_override"] = b"wrong"
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 128), "blue").save(image)
    with pytest.raises(RuntimeError, match="写入回读不一致"):
        WindowsWechatDriver._set_clipboard_image(image)
    assert state["events"] == ["open", "empty", "write", "close"]


def install_editor(driver, monkeypatch, *, initially_focused=False, focus_succeeds=True, count=1):
    state = {"focused": initially_focused, "set_focus": 0}
    def focus():
        state["set_focus"] += 1
        state["focused"] = focus_succeeds
    rect = SimpleNamespace(left=100, top=700, right=900, bottom=900)
    editor = SimpleNamespace(element_info=SimpleNamespace(automation_id="chat_input_field", rectangle=rect), is_visible=lambda: True, has_keyboard_focus=lambda: state["focused"], set_focus=focus)
    root = SimpleNamespace(descendants=lambda **_: [editor] * count)
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Desktop=lambda **_: SimpleNamespace(window=lambda **_: root)))
    monkeypatch.setattr(driver, "_read_send_region_boxes", lambda: ((100, 700, 900, 900), (100, 100, 900, 690)))
    return state


@pytest.mark.parametrize("already_focused", [False, True])
def test_image_composer_confirms_actual_focus_and_only_refocuses_if_needed(tmp_path, monkeypatch, already_focused):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_editor(driver, monkeypatch, initially_focused=already_focused)
    driver._ensure_image_composer_focus()
    assert state["focused"]
    assert state["set_focus"] == int(not already_focused)


@pytest.mark.parametrize("count", [0, 2])
def test_image_composer_ambiguity_does_not_set_focus(tmp_path, monkeypatch, count):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_editor(driver, monkeypatch, count=count)
    with pytest.raises(RuntimeError, match="唯一核验"):
        driver._ensure_image_composer_focus()
    assert state["set_focus"] == 0


def test_image_composer_never_pastes_when_focus_cannot_be_proven(tmp_path, monkeypatch):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_editor(driver, monkeypatch, focus_succeeds=False)
    with pytest.raises(RuntimeError, match="未获得图片粘贴焦点"):
        driver._ensure_image_composer_focus()
    assert state["set_focus"] == 1


def test_image_composer_refuses_foreign_window_before_focusing(tmp_path, monkeypatch):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_editor(driver, monkeypatch)
    def foreign():
        raise RuntimeError("前台窗口或群聊已改变")
    monkeypatch.setattr(driver, "_assert_send_context", foreign)
    with pytest.raises(RuntimeError, match="已改变"):
        driver._ensure_image_composer_focus()
    assert state["set_focus"] == 0


@pytest.mark.parametrize("preview", [True, False])
def test_image_paste_uses_verified_clipboard_once_and_requires_preview(tmp_path, monkeypatch, preview):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_clipboard(monkeypatch)
    focus = install_editor(driver, monkeypatch)
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 128), "blue").save(image)
    before = Image.new("RGB", (100, 100), "white")
    staged = Image.new("RGB", (100, 100), "blue")
    keys = []
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_capture_stable_baseline", lambda: (True, before, before, 1))
    monkeypatch.setattr(driver, "_wait_for_staged_change", lambda _: (staged if preview else None, 0.2 if preview else 0.0, 1))
    monkeypatch.setattr(driver, "_wait_for_submission", lambda *_: (True, "verified", {"phase": "submit_verified"}))
    monkeypatch.setattr(driver, "_hotkey", lambda *args: keys.append(args))
    monkeypatch.setattr(driver, "_key", lambda name: keys.append(name))
    result = driver.paste_image(image)
    assert result.success == preview and result.submitted == preview
    assert not result.outcome_unknown
    assert result.diagnostics["clipboard_verified"] and result.diagnostics["composer_focus_verified"]
    assert result.diagnostics["clipboard_sequence"] == 53
    assert set(result.diagnostics["clipboard_formats"]) == {8, 2, 17}
    assert keys == [("ctrl", "v")] + (["enter"] if preview else [])
    assert focus["set_focus"] == 1 and state["events"].count("write") == 1


def test_clipboard_replaced_during_target_check_stops_before_any_paste(tmp_path, monkeypatch):
    driver = driver_for(tmp_path, monkeypatch)
    state = install_clipboard(monkeypatch)
    install_editor(driver, monkeypatch, initially_focused=True)
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 128), "blue").save(image)
    before = Image.new("RGB", (100, 100), "white")
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_capture_stable_baseline", lambda: (True, before, before, 1))
    def context():
        if state["events"].count("write"):
            state["sequence"] += 1
    monkeypatch.setattr(driver, "_assert_send_context", context)
    monkeypatch.setattr(driver, "_hotkey", lambda *_: pytest.fail("剪贴板变化后禁止粘贴"))
    monkeypatch.setattr(driver, "_key", lambda *_: pytest.fail("禁止按发送键"))
    result = driver.paste_image(image)
    assert not result.success and not result.submitted and not result.outcome_unknown
    assert "剪贴板已被更换" in result.detail
    assert state["events"].count("write") == 1


def test_missing_editor_focus_stops_before_touching_draft_or_clipboard(tmp_path, monkeypatch):
    driver = driver_for(tmp_path, monkeypatch)
    install_editor(driver, monkeypatch, focus_succeeds=False)
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: pytest.fail("焦点未确认时禁止复制草稿"))
    monkeypatch.setattr(driver, "_set_clipboard_image", lambda *_: pytest.fail("禁止改写剪贴板"))
    monkeypatch.setattr(driver, "_hotkey", lambda *_: pytest.fail("禁止发送快捷键"))
    result = driver.paste_image(tmp_path / "image.png")
    assert not result.success and not result.submitted and not result.outcome_unknown
    assert "未获得图片粘贴焦点" in result.detail
