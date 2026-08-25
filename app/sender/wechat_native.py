"""微信 4.1.x Windows 原生发送器。

微信 4.1 使用自绘界面，标准 UI Automation 无法可靠定位控件。本模块改用：
键盘搜索 + Windows OCR 精确校验 + 剪贴板粘贴，并在任何歧义或校验失败时停止。

生产入口默认使用 :class:`WindowsWechatDriver`；测试可注入 fake driver，绝不操作桌面。
"""

from __future__ import annotations

import asyncio
import ctypes
import io
import os
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

from app.config.settings import Settings, get_settings
from app.core.logging import get_logger
from app.image.image_task import verify_image
from app.sender.base import SendResult, WechatSender

logger = get_logger("groupbrief.sender")
_PROCESS_LOCK = threading.Lock()
_MUTEX_NAME = "Local\\GroupBrief.WechatDesktopSender"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80


class NativeWechatDriver(Protocol):
    def health_check(self) -> tuple[bool, str]: ...
    def open_and_verify(self, target: str) -> tuple[bool, str]: ...
    def paste_text(self, text: str) -> tuple[bool, str] | "NativeActionResult": ...
    def paste_image(self, image_path: Path) -> tuple[bool, str] | "NativeActionResult": ...


@dataclass(frozen=True)
class OcrLine:
    text: str
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class NativeActionResult:
    success: bool
    detail: str
    submitted: bool = False
    verification_level: str = ""
    outcome_unknown: bool = False


def _now() -> str:
    return datetime.now().isoformat()


def _normalized_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    visible = []
    for char in normalized:
        # Windows OCR 经常省略群名末尾的 Emoji，或只保留变体选择符。
        # 装饰符号不参与目标身份判断，其余标点（如 2.3、UED-4）仍保留。
        if char in {"\u200d", "\ufe0e", "\ufe0f"}:
            continue
        if unicodedata.category(char) in {"So", "Sk"}:
            continue
        visible.append(char)
    compact = re.sub(r"\s+", "", "".join(visible)).strip()
    # Windows OCR 会稳定地把版本号中的点识别成逗号（例如 1.1 → 1，1）。
    # 仅在两个数字之间归一化，避免改变普通群名标点的身份含义。
    return re.sub(r"(?<=\d)[,，、。·](?=\d)", ".", compact)


def _title_matches(value: str, target: str) -> bool:
    """只接受完整群名，或完整群名后跟成员数。"""
    value_n = _normalized_title(value)
    target_n = _normalized_title(target)
    if value_n == target_n:
        return True
    return bool(re.fullmatch(re.escape(target_n) + r"[（(]\d+[)）]", value_n))


def _selected_header_matches(value: str, target: str) -> bool:
    """群聊项已选中后，对标题 OCR 做受限容错复核。"""
    if _title_matches(value, target):
        return True
    value_n = re.sub(r"\(\d+\)$", "", _normalized_title(value))
    target_n = _normalized_title(target)
    if len(target_n) < 8 or abs(len(value_n) - len(target_n)) > 3:
        return False
    if value_n[:3].casefold() != target_n[:3].casefold():
        return False

    suffix_pattern = r"\d+(?:[._-]\d+)+$"
    target_suffix = re.search(suffix_pattern, target_n)
    if target_suffix:
        value_suffix = re.search(suffix_pattern, value_n)
        if value_suffix is None:
            target_digits = re.sub(r"[._-]", "", target_suffix.group(0))
            compact_suffix = re.search(r"(\d+)$", value_n)
            if compact_suffix is None or compact_suffix.group(1) != target_digits:
                return False
        elif value_suffix.group(0) != target_suffix.group(0):
            return False
    return _edit_distance(value_n, target_n) <= min(4, max(1, len(target_n) // 3))


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _has_stable_ascii_anchor(value: str, target: str) -> bool:
    """确认 OCR 仍保留了群名中的英文名或版本号锚点。"""
    value_n = _normalized_title(value).casefold()
    target_n = _normalized_title(target).casefold()
    anchors = re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", target_n)
    return any(len(re.sub(r"[^a-z0-9]", "", anchor)) >= 3 and anchor in value_n for anchor in anchors)


def _search_title_score(value: str, target: str, *, max_distance: int | None = None) -> int | None:
    """返回受限 OCR 距离；版本号不同的相似群名永不匹配。"""
    value_n = _normalized_title(value)
    target_n = _normalized_title(target)
    if _title_matches(value_n, target_n):
        return 0
    anchored = _has_stable_ascii_anchor(value_n, target_n)
    tolerance = max_distance if max_distance is not None else (4 if anchored else 3)
    if len(target_n) < 8 or abs(len(value_n) - len(target_n)) > tolerance:
        return None

    suffix_pattern = r"\d+(?:[._-]\d+)+$"
    target_suffix = re.search(suffix_pattern, target_n)
    if target_suffix:
        value_suffix = re.search(suffix_pattern, value_n)
        if value_suffix is None:
            target_digits = re.sub(r"[._-]", "", target_suffix.group(0))
            compact_suffix = re.search(r"(\d+)$", value_n)
            # Windows OCR 偶尔会把“1.1”群聊项的后半段识别成“1。”。
            # 也可能直接吞掉分隔点，把“2.3”识别成“23”。搜索阶段
            # 只接受完整数字序列一致或首段数字一致的残缺版本；点击后的
            # 标题复核仍要求数字序列一致，不同的 2.3/3.2 永远不会被接受。
            partial_suffix = re.search(r"(\d+)[。.]?$", value_n)
            target_first = target_suffix.group(0).split(".", 1)[0].split("_", 1)[0].split("-", 1)[0]
            compact_matches = compact_suffix is not None and compact_suffix.group(1) == target_digits
            partial_matches = partial_suffix is not None and partial_suffix.group(1) == target_first
            if not compact_matches and not partial_matches:
                return None
        elif value_suffix.group(0) != target_suffix.group(0):
            return None

    distance = _edit_distance(value_n, target_n)
    return distance if distance <= tolerance else None


def _section_label(value: str) -> str:
    return _normalized_title(value).strip("[]【】()（）<>《》")


def _select_group_search_match(lines: list[OcrLine], target: str) -> tuple[OcrLine | None, str]:
    """只从搜索浮层的“群聊”分区选择目标。

    微信 4.1 的绿色群名 OCR 不稳定，“群聊”标题本身也可能被误识别；
    “聊天记录”标题较稳定，因此用它作为群聊分区的下边界。群聊结果是
    该边界前最后一个、且紧邻边界的完整标题。网络结果和输入框中的同名
    文本距离边界更远，不会成为候选。
    """
    ordered = sorted(lines, key=lambda line: (line.top, line.left))
    chat_sections = [line for line in ordered if _section_label(line.text) == "聊天记录"]
    candidates: list[OcrLine] = []
    if len(chat_sections) == 1:
        chat_top = chat_sections[0].top
        candidates = [
            line
            for line in ordered
            if line.top + line.height <= chat_top
            and 0 <= chat_top - (line.top + line.height) <= max(line.height * 6.0, 120.0)
        ]

    scored = [(score, line) for line in candidates if (score := _search_title_score(line.text, target)) is not None]
    if not scored and not chat_sections:
        # 新改名或很少在聊天中提及的群，搜索结果可能只有
        # “最常使用”中的群聊项，没有“聊天记录”分区。此时只允许
        # 选择“搜索网络结果”之前、且明显低于顶部输入框的唯一匹配。
        network_sections = [line for line in ordered if "搜索网络结果" in _section_label(line.text)]
        if network_sections:
            network_top = min(line.top for line in network_sections)
            before_network = [
                line
                for line in ordered
                if line.top >= 55.0 and line.top + line.height <= network_top
            ]
            scored = [
                (score, line)
                for line in before_network
                # 这个候选已被“顶部输入框之下 + 网络结果之前”
                # 双重限定，而且点击后还要复核聊天标题，可额外容忍
                # “V4.0”被识别成“V4℃”时产生的一个 OCR 距离。
                if (score := _search_title_score(line.text, target, max_distance=5)) is not None
            ]
    if not scored:
        return None, "群聊分区未得到可验证的目标匹配（匹配数 0）"
    best_score = min(score for score, _ in scored)
    best = [line for score, line in scored if score == best_score]
    if len(best) != 1:
        return None, f"群聊分区目标仍有歧义（最佳匹配数 {len(best)}）"
    return best[0], ""


def _coerce_action_result(value: tuple[bool, str] | NativeActionResult) -> NativeActionResult:
    if isinstance(value, NativeActionResult):
        return value
    ok, detail = value
    return NativeActionResult(
        success=bool(ok),
        detail=str(detail),
        submitted=bool(ok),
        verification_level="ui_observed" if ok else "",
        outcome_unknown=False,
    )


def _main_chat_horizontal_bounds(left: int, right: int) -> tuple[int, int]:
    """返回中间聊天区的安全水平范围，避开左侧会话列表和可选右侧面板。"""
    width = right - left
    content_left = left + int(min(max(width * 0.25, 430.0), 580.0))
    content_right = right if width <= 1800 else left + min(int(width * 0.65), 1400)
    if content_right - content_left < 320:
        content_right = right
    return content_left, content_right


@contextmanager
def _desktop_mutex(timeout_seconds: float) -> Iterator[None]:
    """进程内锁 + Windows 命名互斥锁，避免文字和图片被其他任务穿插。"""
    if not _PROCESS_LOCK.acquire(timeout=max(timeout_seconds, 0.1)):
        raise TimeoutError("另一个微信发送任务正在运行")
    handle = None
    try:
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
            if not handle:
                raise OSError("无法创建微信发送互斥锁")
            wait_code = kernel32.WaitForSingleObject(handle, int(timeout_seconds * 1000))
            if wait_code not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
                raise TimeoutError("等待微信发送互斥锁超时")
        yield
    finally:
        if handle:
            ctypes.windll.kernel32.ReleaseMutex(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
        _PROCESS_LOCK.release()


class WindowsWechatDriver:
    """真实桌面驱动；依赖均延迟导入，服务器在非 Windows 环境仍可启动。"""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.delay = max(float(self.settings.wechat_native_action_delay_seconds), 0.2)
        self._window: int | None = None
        self._enable_dpi_awareness()

    def health_check(self) -> tuple[bool, str]:
        report = self.health_report()
        if report["ok"]:
            return True, "微信原生发送器可用（依赖、桌面、中文 OCR、剪贴板、唯一窗口均通过）"
        for stage in ("dependencies", "desktop", "ocr", "clipboard", "window"):
            item = report[stage]
            if not item["ok"]:
                return False, item["detail"]
        return False, "微信原生发送器健康检查失败"

    def health_report(self) -> dict:
        stages = {
            "dependencies": {"ok": False, "detail": "尚未检查"},
            "desktop": {"ok": False, "detail": "尚未检查"},
            "ocr": {"ok": False, "detail": "尚未检查"},
            "clipboard": {"ok": False, "detail": "尚未检查"},
            "window": {"ok": False, "detail": "尚未检查"},
        }
        if os.name != "nt":
            stages["dependencies"] = {"ok": False, "detail": "Windows 原生微信发送器仅支持 Windows"}
            return {"ok": False, **stages}
        try:
            self._imports()
        except Exception as exc:
            stages["dependencies"] = {"ok": False, "detail": f"Windows 微信发送依赖不可用：{exc}"}
            return {"ok": False, **stages}
        stages["dependencies"] = {"ok": True, "detail": "Pillow、pywin32、WinRT/OCR 依赖可导入"}

        unlocked = self._desktop_unlocked()
        stages["desktop"] = {
            "ok": unlocked,
            "detail": "Windows 桌面已解锁" if unlocked else "Windows 桌面已锁定，已停止微信自动发送",
        }
        try:
            language = asyncio.run(self._ocr_language())
            ocr_ok = language.lower().startswith("zh")
            stages["ocr"] = {
                "ok": ocr_ok,
                "detail": f"Windows OCR 语言：{language}" if ocr_ok else f"Windows OCR 未启用中文识别（当前 {language or '未知'}）",
            }
        except Exception as exc:
            stages["ocr"] = {"ok": False, "detail": f"Windows OCR 不可用：{exc}"}
        try:
            self._check_clipboard()
            stages["clipboard"] = {"ok": True, "detail": "Windows 剪贴板可打开"}
        except Exception as exc:
            stages["clipboard"] = {"ok": False, "detail": f"Windows 剪贴板不可用：{exc}"}
        try:
            windows = self._wechat_windows()
            window_ok = len(windows) == 1
            stages["window"] = {
                "ok": window_ok,
                "detail": "找到唯一可见微信主窗口" if window_ok else f"必须存在唯一可见微信主窗口，当前找到 {len(windows)} 个",
            }
        except Exception as exc:
            stages["window"] = {"ok": False, "detail": f"微信窗口检查失败：{exc}"}
        return {"ok": all(item["ok"] for item in stages.values()), **stages}

    def open_and_verify(self, target: str) -> tuple[bool, str]:
        target = (target or "").strip()
        if not target:
            return False, "发送目标为空"
        ok, detail = self.health_check()
        if not ok:
            return False, detail
        windows = self._wechat_windows()
        self._window = windows[0]
        activated = False
        for _ in range(3):
            if self._activate(self._window):
                activated = True
                break
            time.sleep(self.delay)
        if not activated:
            return False, "微信窗口无法激活"

        left, top, right, bottom = self._window_rect(self._window)
        width, height = right - left, bottom - top
        if width < 600 or height < 420:
            return False, "微信窗口尺寸异常，请恢复主窗口后重试"

        # 微信 4.1.x 不再保证 Ctrl+F 会聚焦全局搜索。直接点击左上角搜索框，
        # 坐标限制在其稳定区域内，兼容不同窗口宽度与 DPI。
        search_x = left + min(max(width * 0.21, 180.0), 360.0)
        search_y = top + min(max(height * 0.062, 40.0), 90.0)
        self._click(search_x, search_y)
        time.sleep(self.delay)
        # 上一次验证异常退出时搜索框可能保留旧内容；每次都覆盖输入，避免
        # 重试把目标名称重复拼接后造成误判或点错会话。
        self._hotkey("ctrl", "a")
        self._set_clipboard_text(target)
        self._hotkey("ctrl", "v")
        time.sleep(self.delay * 1.5)

        chat_left, chat_right = _main_chat_horizontal_bounds(left, right)
        header_box = (chat_left, top, chat_right, top + int(height * 0.16))
        if any(_title_matches(line.text, target) for line in self._ocr_screen(header_box)):
            return True, f"当前聊天标题已精确验证：{target}"

        # 覆盖完整搜索浮层，再按分区选择群聊项；不能把顶部网络结果或下方
        # 聊天记录里的同名文本当作会话目标。
        search_box = (
            left,
            top + int(height * 0.05),
            left + min(int(width * 0.50), 700),
            min(bottom, top + int(height * 0.65)),
        )
        matched = None
        match_error = "搜索结果尚未完成识别"
        search_attempts = max(3, min(6, int(1.5 / self.delay)))
        for _ in range(search_attempts):
            lines = self._ocr_screen(search_box)
            matched, match_error = _select_group_search_match(lines, target)
            if matched is not None:
                break
            time.sleep(self.delay)
        if matched is None:
            return False, f"{match_error}，已停止发送"
        self._click(search_box[0] + matched.left + matched.width / 2, search_box[1] + matched.top + matched.height / 2)
        time.sleep(self.delay * 1.5)

        header_attempts = max(3, min(10, int(2.0 / self.delay)))
        for _ in range(header_attempts):
            header_lines = self._ocr_screen(header_box)
            if any(_selected_header_matches(line.text, target) for line in header_lines):
                return True, f"已精确查找并验证目标：{target}"
            time.sleep(self.delay)
        return False, "聊天标题 OCR 校验失败，已停止发送"

    def paste_text(self, text: str) -> NativeActionResult:
        if not text.strip():
            return NativeActionResult(False, "发送文字为空")
        submitted = False
        try:
            before_composer, before_chat = self._capture_send_regions()
            self._focus_composer()
            self._set_clipboard_text(text)
            self._hotkey("ctrl", "v")
            time.sleep(self.delay)
            staged_composer, _ = self._capture_send_regions()
            self._key("enter")
            submitted = True
            time.sleep(self.delay * 1.5)
            after_composer, after_chat = self._capture_send_regions()
            ok, detail = self._verify_submission(
                before_composer, staged_composer, after_composer, before_chat, after_chat
            )
            if not ok:
                return NativeActionResult(False, f"文字已按 Enter，但 {detail}", True, "unknown", True)
            return NativeActionResult(True, "文字已提交，且输入区清空和聊天区域变化已由 UI 观察", True, "ui_observed")
        except Exception as exc:
            return NativeActionResult(
                False,
                f"文字发送失败：{exc}",
                submitted,
                "unknown" if submitted else "",
                submitted,
            )

    def paste_image(self, image_path: Path) -> NativeActionResult:
        submitted = False
        try:
            before_composer, before_chat = self._capture_send_regions()
            self._focus_composer()
            self._set_clipboard_image(image_path)
            self._hotkey("ctrl", "v")
            time.sleep(self.delay * 1.5)
            staged_composer, _ = self._capture_send_regions()
            if self._difference_ratio(before_composer, staged_composer) < 0.0005:
                return NativeActionResult(False, "图片粘贴后未观察到预览，已停止发送")
            if not self._window:
                return NativeActionResult(False, "微信窗口尚未验证")
            self._key("enter")
            submitted = True
            time.sleep(self.delay * 2.5)
            after_composer, after_chat = self._capture_send_regions()
            ok, detail = self._verify_submission(
                before_composer, staged_composer, after_composer, before_chat, after_chat
            )
            if not ok:
                return NativeActionResult(False, f"图片已按 Enter，但 {detail}", True, "unknown", True)
            return NativeActionResult(True, "图片已提交，且预览清空和聊天区域变化已由 UI 观察", True, "ui_observed")
        except Exception as exc:
            return NativeActionResult(
                False,
                f"图片发送失败：{exc}",
                submitted,
                "unknown" if submitted else "",
                submitted,
            )

    @staticmethod
    def _imports():
        import win32api  # noqa: F401
        import win32clipboard  # noqa: F401
        import win32con  # noqa: F401
        import win32gui  # noqa: F401
        from PIL import Image, ImageGrab  # noqa: F401
        import winrt.windows.foundation  # noqa: F401
        import winrt.windows.foundation.collections  # noqa: F401
        from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: F401
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream  # noqa: F401

    @staticmethod
    def _enable_dpi_awareness() -> None:
        """让 Win32 窗口坐标与 Pillow 在多屏缩放环境中的物理像素一致。"""
        if os.name != "nt":
            return
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            # 进程若已由宿主设置 DPI 模式，Windows 会拒绝再次设置；此时
            # 继续沿用宿主上下文，由后续 OCR 精确校验负责失败关闭。
            logger.debug("无法覆盖进程 DPI 感知上下文，沿用当前设置", exc_info=True)

    @staticmethod
    def _desktop_unlocked() -> bool:
        user32 = ctypes.windll.user32
        desktop = user32.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            return False
        user32.CloseDesktop(desktop)
        return True

    @staticmethod
    async def _ocr_language() -> str:
        from winrt.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows OCR 引擎不可用")
        language = getattr(engine, "recognizer_language", None)
        return str(getattr(language, "language_tag", "") or "")

    @staticmethod
    def _check_clipboard() -> None:
        import win32clipboard

        win32clipboard.OpenClipboard()
        win32clipboard.CloseClipboard()

    @staticmethod
    def _wechat_windows() -> list[int]:
        import win32gui

        matches: list[int] = []

        def visit(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if title in {"微信", "WeChat"}:
                matches.append(hwnd)

        win32gui.EnumWindows(visit, None)
        return matches

    @staticmethod
    def _activate(hwnd: int) -> bool:
        import win32con
        import win32gui

        attached_threads: list[int] = []
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            if win32gui.GetForegroundWindow() == hwnd:
                return True

            # Windows 会阻止普通后台进程直接抢前台。临时把当前线程连接到
            # 当前前台窗口和微信窗口的输入队列，完成激活后立即解除。
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            current_thread = int(kernel32.GetCurrentThreadId())
            foreground = win32gui.GetForegroundWindow()
            thread_ids = {
                int(user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0,
                int(user32.GetWindowThreadProcessId(hwnd, None)),
            }
            for thread_id in thread_ids:
                if thread_id and thread_id != current_thread:
                    if user32.AttachThreadInput(current_thread, thread_id, True):
                        attached_threads.append(thread_id)

            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
            time.sleep(0.1)
            return win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False
        finally:
            if attached_threads:
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                current_thread = int(kernel32.GetCurrentThreadId())
                for thread_id in reversed(attached_threads):
                    user32.AttachThreadInput(current_thread, thread_id, False)

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
        import win32gui

        return win32gui.GetWindowRect(hwnd)

    @staticmethod
    def _click(x: float, y: float) -> None:
        import win32api
        import win32con

        win32api.SetCursorPos((int(x), int(y)))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)

    def _focus_composer(self) -> None:
        if not self._window:
            raise RuntimeError("微信窗口尚未验证")
        left, top, right, bottom = self._window_rect(self._window)
        chat_left, chat_right = _main_chat_horizontal_bounds(left, right)
        self._click((chat_left + chat_right) / 2, bottom - min(120, (bottom - top) * 0.18))
        time.sleep(self.delay)

    def _capture_send_regions(self):
        if not self._window:
            raise RuntimeError("微信窗口尚未验证")
        from PIL import ImageGrab

        left, top, right, bottom = self._window_rect(self._window)
        height = bottom - top
        content_left, content_right = _main_chat_horizontal_bounds(left, right)
        composer_top = bottom - min(250, int(height * 0.30))
        composer = ImageGrab.grab(
            bbox=(content_left, composer_top, content_right, bottom - 35), all_screens=True
        ).convert("RGB")
        chat = ImageGrab.grab(
            bbox=(content_left, top + min(100, int(height * 0.15)), content_right, composer_top), all_screens=True
        ).convert("RGB")
        return composer, chat

    @staticmethod
    def _difference_ratio(first, second) -> float:
        from PIL import ImageChops, ImageStat

        if first.size != second.size:
            return 1.0
        difference = ImageChops.difference(first, second)
        means = ImageStat.Stat(difference).mean
        return sum(means) / (255.0 * max(len(means), 1))

    @classmethod
    def _verify_submission(cls, before_composer, staged_composer, after_composer, before_chat, after_chat) -> tuple[bool, str]:
        staged_change = cls._difference_ratio(before_composer, staged_composer)
        cleared_change = cls._difference_ratio(staged_composer, after_composer)
        returned_toward_empty = cls._difference_ratio(before_composer, after_composer)
        chat_change = cls._difference_ratio(before_chat, after_chat)
        empty_delta_limit = min(max(staged_change * 0.25, 0.0003), 0.0015)
        composer_ok = (
            staged_change >= 0.0005
            and cleared_change >= 0.0005
            and returned_toward_empty <= empty_delta_limit
        )
        chat_ok = chat_change >= 0.0003
        if composer_ok and chat_ok:
            return True, "UI 已观察到提交变化"
        return False, (
            "未同时观察到输入区/图片预览清空与聊天区域变化"
            f"（composer={staged_change:.5f}/{cleared_change:.5f}/{returned_toward_empty:.5f}"
            f"<=limit {empty_delta_limit:.5f}, chat={chat_change:.5f}）"
        )

    @staticmethod
    def _key(name: str, key_up: bool = False) -> None:
        import win32api
        import win32con

        codes = {
            "ctrl": win32con.VK_CONTROL,
            "a": ord("A"),
            "f": ord("F"),
            "v": ord("V"),
            "enter": win32con.VK_RETURN,
        }
        win32api.keybd_event(codes[name], 0, win32con.KEYEVENTF_KEYUP if key_up else 0, 0)

    def _hotkey(self, modifier: str, key: str) -> None:
        self._key(modifier)
        self._key(key)
        self._key(key, key_up=True)
        self._key(modifier, key_up=True)

    @staticmethod
    def _set_clipboard_text(text: str) -> None:
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def _set_clipboard_image(image_path: Path) -> None:
        import win32clipboard
        import win32con
        from PIL import Image

        with Image.open(image_path) as image:
            stream = io.BytesIO()
            image.convert("RGB").save(stream, "BMP")
            dib = stream.getvalue()[14:]
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()

    @staticmethod
    def _ocr_screen(bbox: tuple[int, int, int, int]) -> list[OcrLine]:
        from PIL import ImageGrab

        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return asyncio.run(WindowsWechatDriver._ocr_png(buffer.getvalue()))

    @staticmethod
    async def _ocr_png(data: bytes) -> list[OcrLine]:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(data)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows OCR 未安装中文语言支持")
        result = await engine.recognize_async(bitmap)
        lines: list[OcrLine] = []
        for line in result.lines:
            words = list(line.words)
            if not words:
                continue
            left = min(word.bounding_rect.x for word in words)
            top = min(word.bounding_rect.y for word in words)
            right = max(word.bounding_rect.x + word.bounding_rect.width for word in words)
            bottom = max(word.bounding_rect.y + word.bounding_rect.height for word in words)
            lines.append(OcrLine(line.text, left, top, right - left, bottom - top))
        return lines


class WechatNativeSender(WechatSender):
    name = "wechat_native"

    def __init__(self, settings: Settings | None = None, driver: NativeWechatDriver | None = None, dry_run: bool = False):
        self.settings = settings or get_settings()
        self.driver = driver or WindowsWechatDriver(self.settings)
        self.dry_run = dry_run

    def health_check(self, report: dict | None = None) -> tuple[bool, str]:
        if self.dry_run:
            return True, "Windows 微信原生发送器 dry-run"
        report = report or self.health_report()
        if report["ok"]:
            return True, "微信原生发送器可用（依赖、桌面、中文 OCR、剪贴板、唯一窗口均通过）"
        for stage in ("dependencies", "desktop", "ocr", "clipboard", "window"):
            item = report[stage]
            if not item["ok"]:
                return False, item["detail"]
        return False, "微信原生发送器健康检查失败"

    def health_report(self) -> dict:
        if self.dry_run:
            return {
                "ok": True,
                "dependencies": {"ok": True, "detail": "dry-run"},
                "desktop": {"ok": True, "detail": "dry-run"},
                "ocr": {"ok": True, "detail": "dry-run"},
                "clipboard": {"ok": True, "detail": "dry-run"},
                "window": {"ok": True, "detail": "dry-run"},
            }
        report_method = getattr(self.driver, "health_report", None)
        if callable(report_method):
            return report_method()
        ok, detail = self.driver.health_check()
        return {
            "ok": ok,
            "dependencies": {"ok": ok, "detail": detail},
            "desktop": {"ok": ok, "detail": "由 Provider 汇总检查"},
            "ocr": {"ok": ok, "detail": "由 Provider 汇总检查"},
            "clipboard": {"ok": ok, "detail": "由 Provider 汇总检查"},
            "window": {"ok": ok, "detail": "由 Provider 汇总检查"},
        }

    def verify_target(self, target: str) -> tuple[bool, str]:
        if self.dry_run:
            return True, f"[dry-run] 目标验证：{target}"
        try:
            with _desktop_mutex(self.settings.wechat_native_mutex_timeout_seconds):
                return self.driver.open_and_verify(target)
        except Exception as exc:
            return False, str(exc)

    def send_text(self, target: str, text: str) -> SendResult:
        text_result, _ = self.send_bundle(target, text, None)
        return text_result

    def send_image(self, target: str, image_path) -> SendResult:
        image_result = SendResult(False, "图片未发送", _now())
        path = Path(image_path)
        image_ok, image_detail = verify_image(path)
        if not image_ok:
            return SendResult(False, image_detail, _now())
        if self.dry_run:
            return SendResult(True, f"[dry-run] 发送图片到 {target}", _now(), False, "dry_run")
        try:
            with _desktop_mutex(self.settings.wechat_native_mutex_timeout_seconds):
                ok, detail = self.driver.open_and_verify(target)
                if not ok:
                    return SendResult(False, detail, _now())
                action = _coerce_action_result(self.driver.paste_image(path.resolve()))
                image_result = SendResult(
                    action.success,
                    action.detail,
                    _now(),
                    action.submitted,
                    action.verification_level,
                    action.outcome_unknown,
                )
        except Exception as exc:
            image_result = SendResult(False, str(exc), _now())
        return image_result

    def send_bundle(self, target: str, text: str, image_path: str | Path | None) -> tuple[SendResult, SendResult | None]:
        path = Path(image_path) if image_path is not None else None
        if path is not None:
            image_ok, image_detail = verify_image(path)
            if not image_ok:
                return SendResult(False, "未发送文字：图片无效", _now()), SendResult(False, image_detail, _now())
        if self.dry_run:
            return SendResult(True, f"[dry-run] 发送文字到 {target}", _now(), False, "dry_run"), (
                SendResult(True, f"[dry-run] 发送图片到 {target}", _now(), False, "dry_run") if path else None
            )
        try:
            with _desktop_mutex(self.settings.wechat_native_mutex_timeout_seconds):
                ok, detail = self.driver.open_and_verify(target)
                if not ok:
                    return SendResult(False, detail, _now()), None
                text_action = _coerce_action_result(self.driver.paste_text(text))
                text_result = SendResult(
                    text_action.success,
                    text_action.detail,
                    _now(),
                    text_action.submitted,
                    text_action.verification_level,
                    text_action.outcome_unknown,
                )
                if not text_action.success or path is None:
                    return text_result, None
                image_action = _coerce_action_result(self.driver.paste_image(path.resolve()))
                return text_result, SendResult(
                    image_action.success,
                    image_action.detail,
                    _now(),
                    image_action.submitted,
                    image_action.verification_level,
                    image_action.outcome_unknown,
                )
        except Exception as exc:
            return SendResult(False, str(exc), _now()), None


def validate_wechat_sender_mode(settings: Settings) -> str:
    """返回规范化 sender mode；未知值禁止默认落到 native。"""
    mode = str(settings.wechat_sender_mode or "").strip().lower()
    if mode not in {"native", "legacy_cli"}:
        raise ValueError(f"不支持的微信发送 Provider：{settings.wechat_sender_mode}")
    return mode


def create_wechat_sender(settings: Settings | None = None, dry_run: bool = False) -> WechatSender:
    settings = settings or get_settings()
    mode = validate_wechat_sender_mode(settings)
    if mode == "legacy_cli":
        from app.sender.wechat_automation import WechatAutomationSender

        return WechatAutomationSender(settings=settings, dry_run=dry_run)
    if mode == "native":
        return WechatNativeSender(settings=settings, dry_run=dry_run)
    raise AssertionError("微信 sender 配置校验未覆盖已知类型")
