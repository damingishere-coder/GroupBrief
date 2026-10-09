from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from fastapi import HTTPException
from sqlmodel import Session

from app.config.settings import Settings
from app.sender import wechat_account as accounts
from app.sender.wechat_native import WindowsWechatDriver, WechatNativeSender, NativeActionResult


def picture(color="red"):
    image = Image.new("RGB", (64, 64), "black")
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 8, 46, 43), fill=color)
    draw.polygon([(23, 37), (48, 56), (13, 56)], fill="white")
    draw.rectangle((25, 17, 29, 21), fill="black")
    return image


class Driver:
    def __init__(self, settings, images, hidden=()):
        self.settings, self.images = settings, images
        self.hidden = list(hidden)
        self.unlocked = True
        self.restored = []
    def _desktop_unlocked(self): return self.unlocked
    def _wechat_windows(self): return [handle for handle in self.images if handle not in self.hidden]
    def _hidden_wechat_windows(self): return self.hidden
    def _account_window_identity(self, hwnd): return {"hwnd": hwnd, "pid": hwnd * 100, "created": "stable"}
    def _capture_account_avatar(self, hwnd, *, restore=False):
        if hwnd in self.hidden:
            if not restore: raise ValueError("需要恢复")
            self.restored.append(hwnd)
            self.hidden.remove(hwnd)
        image = self.images[hwnd]
        if isinstance(image, Exception): raise image
        return image


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'test.db'}")


def bind_reference(settings, image=None):
    data = accounts.avatar_bytes(image or picture())
    digest = hashlib.sha256(data).hexdigest()
    path = accounts.reference_path(settings, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    settings.wechat_sender_account_binding = json.dumps({"version": 1, "account_name": "大明同学", "avatar_sha256": digest, "bound_at": "now"})


@pytest.mark.parametrize("scale", [1, 1.25, 1.5, 1.75, 2])
def test_same_avatar_at_common_scales(scale):
    reference = picture()
    observed = reference.resize((round(64 * scale), round(64 * scale)), Image.Resampling.LANCZOS)
    assert accounts.compare_avatars(reference, observed)["matched"]


def test_same_shape_different_color_is_rejected():
    result = accounts.compare_avatars(picture(), picture("blue"))
    assert result["hash_distance"] <= accounts.HASH_LIMIT
    assert not result["matched"]


def test_edges_do_not_define_identity():
    image = picture()
    ImageDraw.Draw(image).rectangle((0, 0, 6, 6), fill="white")
    assert accounts.compare_avatars(picture(), image)["matched"]


@pytest.mark.parametrize("color", ["white", "black", "gray"])
def test_blank_avatar_is_rejected(color):
    with pytest.raises(ValueError, match="空白"):
        accounts.normalized_avatar(Image.new("RGB", (64, 64), color))


def test_selects_avatar_instead_of_first_window(settings):
    bind_reference(settings)
    result = accounts.inspect_account(Driver(settings, {1: picture("blue"), 2: picture()}))
    assert result["ok"] and result["identity"]["hwnd"] == 2


@pytest.mark.parametrize("images,count", [({1: picture("blue")}, 0), ({1: picture(), 2: picture()}, 2)])
def test_zero_or_multiple_matches_stop(settings, images, count):
    bind_reference(settings)
    result = accounts.inspect_account(Driver(settings, images))
    assert not result["ok"] and result["match_count"] == count


def test_unreadable_other_window_blocks_selection(settings):
    bind_reference(settings)
    result = accounts.inspect_account(Driver(settings, {1: picture(), 2: ValueError("blank")}))
    assert not result["ok"] and result["match_count"] == 1


def test_binding_required_even_for_only_one_window(settings):
    assert not accounts.inspect_account(Driver(settings, {1: picture()}))["ok"]


def test_restore_hidden_existing_windows(settings):
    bind_reference(settings)
    driver = Driver(settings, {1: picture("blue"), 2: picture()}, hidden=(1, 2))
    assert accounts.inspect_account(driver, restore=True)["ok"]
    assert driver.restored == [1, 2]


def test_locked_desktop_never_reads_windows(settings):
    bind_reference(settings)
    driver = Driver(settings, {1: picture()}, hidden=(1,))
    driver.unlocked = False
    assert not accounts.inspect_account(driver, restore=True)["ok"]
    assert not driver.restored


def test_damaged_reference_stops(settings):
    bind_reference(settings)
    metadata = json.loads(settings.wechat_sender_account_binding)
    accounts.reference_path(settings, metadata["avatar_sha256"]).write_bytes(b"bad")
    assert not accounts.binding_status(settings)["bound"]


def test_capture_changes_between_reads_rejected(settings):
    driver = Driver(settings, {1: picture()})
    reads = iter([picture(), picture("blue")])
    driver._capture_account_avatar = lambda *args, **kwargs: next(reads)
    assert not accounts.scan_windows(driver, restore=True)[0]["ok"]


def test_preview_requires_fresh_unchanged_window(settings):
    driver = Driver(settings, {1: picture()})
    token = accounts.candidate_previews(driver)["candidates"][0]["candidate_id"]
    driver.images[1] = picture("blue")
    with pytest.raises(ValueError, match="头像已变化"):
        accounts.prepare_binding(driver, token, "大明同学")
    driver.images.clear()
    with pytest.raises(ValueError, match="已关闭"):
        accounts.prepare_binding(driver, token, "大明同学")


def test_expired_preview_rejected(settings, monkeypatch):
    driver = Driver(settings, {1: picture()})
    token = accounts.candidate_previews(driver)["candidates"][0]["candidate_id"]
    monkeypatch.setattr(accounts.time, "monotonic", lambda: accounts._candidates[token]["created"] + 301)
    with pytest.raises(ValueError, match="过期"):
        accounts.prepare_binding(driver, token, "大明同学")


def test_reused_handle_rejected(settings):
    driver = Driver(settings, {1: picture()})
    token = accounts.candidate_previews(driver)["candidates"][0]["candidate_id"]
    driver._account_window_identity = lambda hwnd: {"hwnd": hwnd, "pid": 222, "created": "new"}
    with pytest.raises(ValueError, match="窗口已变化"):
        accounts.prepare_binding(driver, token, "大明同学")


def test_binding_persists_and_generic_settings_cannot_replace_it(settings, monkeypatch):
    from app.api import wechat_account as api
    from app.api import settings as settings_api
    from app.db import repository as repo
    monkeypatch.setattr(repo, "engine", repo.engine)
    repo.init_db(settings)
    driver = Driver(settings, {1: picture()})
    token = accounts.candidate_previews(driver)["candidates"][0]["candidate_id"]
    monkeypatch.setattr(api, "WindowsWechatDriver", lambda *args: driver)
    monkeypatch.setattr(settings_api, "get_runtime_settings", lambda: settings)
    with Session(repo.engine) as session:
        response = api.bind(api.AccountBindingRequest(candidate_id=token, account_name="大明同学"), settings, session)
        assert response["bound"]
        saved = settings.wechat_sender_account_binding
        settings_api.update_settings(settings_api.SettingsPayload(values={accounts.BINDING_KEY: "fake"}), session)
        assert settings.wechat_sender_account_binding == saved
        restored = Settings(_env_file=None, database_url=settings.database_url)
        repo.apply_db_settings(restored)
        assert accounts.binding_status(restored)["avatar_sha256"] == response["avatar_sha256"]


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_context_loss_before_paste_or_enter_never_submits(settings, monkeypatch, stage):
    driver = WindowsWechatDriver(settings)
    driver._window = 1
    checks, pressed, clipboard = [], [], []
    def guard(**kwargs):
        checks.append(1)
        if len(checks) == stage: raise ValueError("账号已变化")
    before, staged = Image.new("RGB", (20, 20), "white"), Image.new("RGB", (20, 20), "black")
    monkeypatch.setattr(driver, "_assert_send_context", guard)
    monkeypatch.setattr(driver, "_focus_composer", lambda: None)
    monkeypatch.setattr(driver, "_composer_is_empty", lambda: (True, "empty"))
    monkeypatch.setattr(driver, "_capture_stable_baseline", lambda: (True, before, before, 1))
    monkeypatch.setattr(driver, "_set_clipboard_text", clipboard.append)
    monkeypatch.setattr(driver, "_set_clipboard_image", clipboard.append)
    monkeypatch.setattr(driver, "_hotkey", lambda *args: None)
    monkeypatch.setattr(driver, "_wait_for_staged_change", lambda *args: (staged, .1, 1))
    monkeypatch.setattr(driver, "_key", pressed.append)
    for action in (lambda: driver.paste_text("text"), lambda: driver.paste_image("image.png")):
        checks.clear(); clipboard.clear()
        result = action()
        assert not result.success and not result.submitted and not result.outcome_unknown
        assert pressed == []
        if stage < 3: assert not clipboard


def test_guard_rejects_wrong_foreground_and_changed_target(settings, monkeypatch):
    import win32gui
    driver = WindowsWechatDriver(settings)
    identity = {"hwnd": 1, "pid": 123}
    driver._window, driver._verified_identity, driver._verified_avatar_sha = 1, identity, "abc"
    driver._verified_target = "目标群"
    monkeypatch.setattr(accounts, "inspect_account", lambda *args, **kwargs: {"ok": True, "detail": "verified", "identity": identity, "avatar_sha256": "abc"})
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 2)
    monkeypatch.setattr(win32gui, "GetAncestor", lambda hwnd, flag: hwnd)
    with pytest.raises(ValueError, match="前台窗口"):
        driver._assert_send_context()
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (0, 0, 1000, 800))
    monkeypatch.setattr(driver, "_read_uia_chat_titles", lambda box: ["其他群"])
    monkeypatch.setattr(driver, "_ocr_screen", lambda box: [])
    with pytest.raises(ValueError, match="目标群"):
        driver._assert_send_context()


def test_text_receipt_preserved_when_image_guard_fails(settings, monkeypatch):
    class BundleDriver:
        def open_and_verify(self, target): return True, "ok"
        def paste_text(self, text): return NativeActionResult(True, "已发送", True, "ui_observed")
        def paste_image(self, path): return NativeActionResult(False, "账号已变化", False)
    monkeypatch.setattr("app.sender.wechat_native.verify_image", lambda path: (True, "ok"))
    text, image = WechatNativeSender(settings, driver=BundleDriver()).send_bundle("group", "text", "image.png")
    assert text.success and text.submitted
    assert not image.success and not image.submitted


def test_bound_account_cannot_fall_back_to_legacy_sender(settings):
    from app.sender.wechat_native import create_wechat_sender
    bind_reference(settings)
    settings.wechat_sender_mode = "legacy_cli"
    with pytest.raises(ValueError, match="不能绕过"):
        create_wechat_sender(settings)


@pytest.mark.parametrize("dx,dy", [(0, 0), (-1800, 400), (2400, -300)])
@pytest.mark.parametrize("dpi", [96, 120, 144, 168, 192])
def test_avatar_capture_box_uses_window_pixels_not_screen_position(settings, monkeypatch, dx, dy, dpi):
    import ctypes
    import win32gui
    import win32ui
    driver = WindowsWechatDriver(settings)
    image, scale = picture(), dpi / 96
    width, height = round(600 * scale), round(450 * scale)
    frame = Image.new("RGB", (width, height), "gray")
    box = tuple(round(value * scale) for value in (19, 42, 55, 78))
    frame.paste(image.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS), box[:2])
    monkeypatch.setattr(driver, "_desktop_unlocked", lambda: True)
    monkeypatch.setattr(driver, "_wechat_windows", lambda: [1])
    monkeypatch.setattr(driver, "_hidden_wechat_windows", lambda: [])
    monkeypatch.setattr(driver, "_window_rect", lambda hwnd: (dx, dy, dx + width, dy + height))
    monkeypatch.setattr(win32gui, "GetClassName", lambda hwnd: "Qt51514QWindowIcon")
    monkeypatch.setattr(win32gui, "IsIconic", lambda hwnd: False)
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(win32gui, "GetWindowDC", lambda hwnd: 1)
    monkeypatch.setattr(win32gui, "ReleaseDC", lambda *args: None)
    monkeypatch.setattr(win32gui, "DeleteObject", lambda *args: None)
    monkeypatch.setattr(ctypes.windll.user32, "GetDpiForWindow", lambda hwnd: dpi)
    monkeypatch.setattr(ctypes.windll.user32, "PrintWindow", lambda *args: 1)
    memory = SimpleNamespace(SelectObject=lambda *args: None, GetSafeHdc=lambda: 1, DeleteDC=lambda: None)
    source = SimpleNamespace(CreateCompatibleDC=lambda: memory, DeleteDC=lambda: None)
    bitmap = SimpleNamespace(CreateCompatibleBitmap=lambda *args: None, GetHandle=lambda: 1,
                             GetBitmapBits=lambda *args: frame.tobytes("raw", "BGRX"))
    monkeypatch.setattr(win32ui, "CreateDCFromHandle", lambda *args: source)
    monkeypatch.setattr(win32ui, "CreateBitmap", lambda: bitmap)
    captured = driver._capture_account_avatar(1)
    assert accounts.compare_avatars(image, captured)["matched"]
