"""微信 4.1.x Windows 原生发送器。

微信 4.1 使用自绘界面，标准 UI Automation 只暴露部分控件。本模块组合：
键盘搜索 + Windows OCR 精确校验 + 唯一 UIA 搜索项兜底 + 剪贴板粘贴，
并在任何歧义或校验失败时停止。

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
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
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
class UiaSearchItem:
    text: str
    automation_id: str
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class NativeActionResult:
    success: bool
    detail: str
    submitted: bool = False
    verification_level: str = ""
    outcome_unknown: bool = False
    diagnostics: dict[str, object] = field(default_factory=dict)


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
    if not _ascii_token_identity_matches(value, target):
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


def _ascii_identity_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 3]


def _ascii_token_identity_matches(value: str, target: str) -> bool:
    """英文群名允许受限 OCR 错字，但每个稳定 token 都必须有对应项。"""

    target_tokens = _ascii_identity_tokens(target)
    if not target_tokens:
        return True
    value_tokens = _ascii_identity_tokens(value)
    return all(
        any(
            _edit_distance(target_token, value_token)
            <= 2
            for value_token in value_tokens
        )
        for target_token in target_tokens
    )


def _search_title_score(value: str, target: str, *, max_distance: int | None = None) -> int | None:
    """返回受限 OCR 距离；版本号不同的相似群名永不匹配。"""
    value_n = _normalized_title(value)
    target_n = _normalized_title(target)
    if _title_matches(value_n, target_n):
        return 0
    if not _ascii_token_identity_matches(value, target):
        return None
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


def _trusted_group_section(value: str) -> bool:
    """微信搜索浮层中允许作为群聊候选上边界的分区。"""

    label = _section_label(value)
    if label == "群聊":
        return True
    # Windows OCR 在绿色标题上偶尔会把“用”识别成形近字（例如“岸”）。
    # 仅对较长且固定的“最常使用”标题容忍一个替换；两字“群聊”仍须精确，
    # 避免把普通聊天文本误当成可信分区。
    return len(label) == len("最常使用") and _edit_distance(label, "最常使用") <= 1


def _select_group_search_match(lines: list[OcrLine], target: str) -> tuple[OcrLine | None, str]:
    """只从搜索浮层的“群聊”分区选择目标。

    微信 4.1 的绿色群名 OCR 不稳定，“群聊”标题本身也可能被误识别；
    “聊天记录”标题较稳定，因此用它作为群聊分区的下边界。群聊结果是
    该边界前最后一个、且紧邻边界的完整标题。网络结果和输入框中的同名
    文本距离边界更远，不会成为候选。
    """
    ordered = sorted(lines, key=lambda line: (line.top, line.left))
    chat_sections = [line for line in ordered if _section_label(line.text) == "聊天记录"]
    trusted_sections = [line for line in ordered if _trusted_group_section(line.text)]
    candidates: list[OcrLine] = []
    if len(chat_sections) == 1:
        chat_top = chat_sections[0].top
        section_before_chat = [
            line for line in trusted_sections if line.top + line.height <= chat_top
        ]
        if section_before_chat:
            section_bottom = max(line.top + line.height for line in section_before_chat)
            candidates = [
                line
                for line in ordered
                if line.top + line.height / 2 >= section_bottom
                and line.top + line.height <= chat_top
                and not _trusted_group_section(line.text)
            ]

    scored = [
        (score, line)
        for line in candidates
        if (score := _search_title_score(line.text, target, max_distance=5)) is not None
    ]
    if not scored and _ascii_identity_tokens(target):
        # 英文群名在绿色搜索项中可能同时出现多个 OCR 错字。只有处在可信
        # 群聊分区、稳定英文 token 仍逐项对应、且最终聊天标题还会再次复核
        # 时，才额外扩大搜索项的距离；中文群名不使用此兜底。
        scored = [
            (score, line)
            for line in candidates
            if (score := _search_title_score(line.text, target, max_distance=8)) is not None
        ]
    if not scored and not chat_sections:
        # 新改名或很少在聊天中提及的群，搜索结果可能只有
        # “最常使用”中的群聊项，没有“聊天记录”分区。此时只允许
        # 选择“搜索网络结果”之前、且明显低于顶部输入框的唯一匹配。
        network_sections = [line for line in ordered if "搜索网络结果" in _section_label(line.text)]
        if network_sections and trusted_sections:
            network_top = min(line.top for line in network_sections)
            before_network = [
                line
                for line in ordered
                if line.top >= 55.0 and line.top + line.height <= network_top
            ]
            section_before_network = [
                line for line in trusted_sections if line.top + line.height <= network_top
            ]
            if section_before_network:
                section_bottom = max(line.top + line.height for line in section_before_network)
                before_network = [
                    line
                    for line in before_network
                    if line.top + line.height / 2 >= section_bottom
                    and not _trusted_group_section(line.text)
                ]
            else:
                before_network = []
            scored = [
                (score, line)
                for line in before_network
                # 这个候选已被“顶部输入框之下 + 网络结果之前”
                # 双重限定，而且点击后还要复核聊天标题，可额外容忍
                # “V4.0”被识别成“V4℃”时产生的一个 OCR 距离。
                if (score := _search_title_score(line.text, target, max_distance=5)) is not None
            ]
            if not scored and _ascii_identity_tokens(target):
                scored = [
                    (score, line)
                    for line in before_network
                    if (score := _search_title_score(line.text, target, max_distance=8)) is not None
                ]
    if not scored:
        return None, (
            "群聊分区未得到可验证的目标匹配"
            f"（匹配数 0；可信分区 {len(trusted_sections)}；聊天记录分区 {len(chat_sections)}）"
        )
    best_score = min(score for score, _ in scored)
    best = [line for score, line in scored if score == best_score]
    if len(best) != 1:
        return None, f"群聊分区目标仍有歧义（最佳匹配数 {len(best)}）"
    return best[0], ""


def _select_uia_group_search_match(
    items: list[UiaSearchItem],
    target: str,
    search_box: tuple[int, int, int, int],
) -> tuple[OcrLine | None, str]:
    """Select one exact WeChat group result exposed by UI Automation.

    WeChat 4.1 gives only the real group-search item an automation id in the
    form ``search_item_<full title>``. Chat-history rows and network results do
    not carry that id. Keep the final header OCR check in ``open_and_verify``
    as a second, independent target verification layer.
    """

    expected_id = f"search_item_{target}"
    box_left, box_top, box_right, box_bottom = search_box
    matches: list[UiaSearchItem] = []
    for item in items:
        center_x = (item.left + item.right) / 2
        center_y = (item.top + item.bottom) / 2
        if (
            item.automation_id == expected_id
            and _title_matches(item.text, target)
            and item.right > item.left
            and item.bottom > item.top
            and box_left <= center_x <= box_right
            and box_top <= center_y <= box_bottom
        ):
            matches.append(item)
    if len(matches) != 1:
        return None, f"UIA 精确群聊项数量不是 1（当前 {len(matches)}）"
    item = matches[0]
    return (
        OcrLine(
            item.text,
            item.left - box_left,
            item.top - box_top,
            item.right - item.left,
            item.bottom - item.top,
        ),
        "",
    )


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
        self.stage_timeout = max(
            float(self.settings.wechat_native_stage_timeout_seconds),
            self.delay,
        )
        self.submit_timeout = max(
            float(self.settings.wechat_native_submit_timeout_seconds),
            self.delay,
        )
        self.poll_interval = min(
            max(float(self.settings.wechat_native_poll_interval_seconds), 0.05),
            1.0,
        )
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
        if os.name != "nt":
            return False, "Windows 原生微信发送器仅支持 Windows"
        try:
            self._imports()
        except Exception as exc:
            return False, f"Windows 微信发送依赖不可用：{exc}"
        if not self._desktop_unlocked():
            return False, "Windows 桌面已锁定，未尝试恢复或操作微信窗口"
        prepared, prepare_detail = self._prepare_wechat_window()
        if not prepared:
            return False, prepare_detail
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
            matched, uia_error = self._find_uia_group_search_match(target, search_box)
            if matched is None and uia_error:
                match_error = f"{match_error}；{uia_error}"
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
            self._focus_composer()
            composer_empty, empty_detail = self._composer_is_empty()
            if not composer_empty:
                return NativeActionResult(
                    False,
                    empty_detail,
                    diagnostics={"phase": "composer_preflight", "composer_empty": False},
                )
            self._focus_composer()
            stable, before_composer, before_chat, baseline_attempts = self._capture_stable_baseline()
            if not stable:
                return NativeActionResult(
                    False,
                    "文字发送前输入区无法达到稳定状态，已停止且未修改可能存在的草稿",
                    diagnostics={
                        "phase": "composer_preflight",
                        "baseline_stable": False,
                        "baseline_attempts": baseline_attempts,
                    },
                )
            self._set_clipboard_text(text)
            self._hotkey("ctrl", "v")
            staged_composer, staged_change, stage_attempts = self._wait_for_staged_change(
                before_composer
            )
            diagnostics = {
                "phase": "composer_staged",
                "baseline_stable": True,
                "baseline_attempts": baseline_attempts,
                "stage_attempts": stage_attempts,
                "staged_change": round(staged_change, 6),
            }
            if staged_composer is None:
                return NativeActionResult(
                    False,
                    "文字粘贴后未观察到输入区暂存，已停止且未按 Enter",
                    diagnostics=diagnostics,
                )
            self._key("enter")
            submitted = True
            ok, detail, submit_diagnostics = self._wait_for_submission(
                before_composer,
                staged_composer,
                before_chat,
            )
            diagnostics.update(submit_diagnostics)
            if not ok:
                return NativeActionResult(
                    False,
                    f"文字已按 Enter，但 {detail}",
                    True,
                    "unknown",
                    True,
                    diagnostics,
                )
            return NativeActionResult(
                True,
                "文字已提交，且输入区清空和聊天区域变化已由 UI 观察",
                True,
                "ui_observed",
                False,
                diagnostics,
            )
        except Exception as exc:
            return NativeActionResult(
                False,
                f"文字发送失败：{exc}",
                submitted,
                "unknown" if submitted else "",
                submitted,
                {"phase": "exception_after_submit" if submitted else "exception_before_submit"},
            )

    def paste_image(self, image_path: Path) -> NativeActionResult:
        submitted = False
        try:
            self._focus_composer()
            composer_empty, empty_detail = self._composer_is_empty()
            if not composer_empty:
                return NativeActionResult(
                    False,
                    empty_detail,
                    diagnostics={"phase": "composer_preflight", "composer_empty": False},
                )
            self._focus_composer()
            stable, before_composer, before_chat, baseline_attempts = self._capture_stable_baseline()
            if not stable:
                return NativeActionResult(
                    False,
                    "图片发送前输入区无法达到稳定状态，已停止且未修改可能存在的草稿",
                    diagnostics={
                        "phase": "composer_preflight",
                        "baseline_stable": False,
                        "baseline_attempts": baseline_attempts,
                    },
                )
            self._set_clipboard_image(image_path)
            self._hotkey("ctrl", "v")
            staged_composer, staged_change, stage_attempts = self._wait_for_staged_change(
                before_composer
            )
            diagnostics = {
                "phase": "composer_staged",
                "baseline_stable": True,
                "baseline_attempts": baseline_attempts,
                "stage_attempts": stage_attempts,
                "staged_change": round(staged_change, 6),
            }
            if staged_composer is None:
                return NativeActionResult(
                    False,
                    "图片粘贴后未观察到预览，已停止且未按 Enter",
                    diagnostics=diagnostics,
                )
            if not self._window:
                return NativeActionResult(False, "微信窗口尚未验证")
            self._key("enter")
            submitted = True
            ok, detail, submit_diagnostics = self._wait_for_submission(
                before_composer,
                staged_composer,
                before_chat,
            )
            diagnostics.update(submit_diagnostics)
            if not ok:
                return NativeActionResult(
                    False,
                    f"图片已按 Enter，但 {detail}",
                    True,
                    "unknown",
                    True,
                    diagnostics,
                )
            return NativeActionResult(
                True,
                "图片已提交，且预览清空和聊天区域变化已由 UI 观察",
                True,
                "ui_observed",
                False,
                diagnostics,
            )
        except Exception as exc:
            return NativeActionResult(
                False,
                f"图片发送失败：{exc}",
                submitted,
                "unknown" if submitted else "",
                submitted,
                {"phase": "exception_after_submit" if submitted else "exception_before_submit"},
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
    def _window_process_name(hwnd: int) -> str:
        """只读取窗口所属进程名；无法确认身份时返回空字符串。"""

        if os.name != "nt":
            return ""
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_process.restype = ctypes.c_void_p
        query_process_image = kernel32.QueryFullProcessImageNameW
        query_process_image.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        query_process_image.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        process_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return ""
        handle = open_process(0x1000, False, process_id.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not query_process_image(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return Path(buffer.value).name.casefold()
        finally:
            close_handle(handle)

    @staticmethod
    def _window_is_current_session(hwnd: int) -> bool:
        """窗口进程必须与发送器处在同一 Windows 会话。"""

        if os.name != "nt":
            return False
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        process_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return False
        window_session = ctypes.c_ulong(0)
        current_session = ctypes.c_ulong(0)
        if not kernel32.ProcessIdToSessionId(process_id.value, ctypes.byref(window_session)):
            return False
        current_pid = kernel32.GetCurrentProcessId()
        if not kernel32.ProcessIdToSessionId(current_pid, ctypes.byref(current_session)):
            return False
        return window_session.value == current_session.value

    @staticmethod
    def _wechat_main_window_class(value: str) -> bool:
        normalized = (value or "").strip()
        return normalized == "WeChatMainWndForPC" or bool(
            re.fullmatch(r"Qt\d+QWindowIcon", normalized)
        )

    @classmethod
    def _enumerate_wechat_windows(cls, *, visible: bool) -> list[int]:
        import win32con
        import win32gui

        matches: list[int] = []

        def visit(hwnd, _):
            if bool(win32gui.IsWindowVisible(hwnd)) is not visible:
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if title not in {"微信", "WeChat"}:
                return
            if win32gui.GetParent(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return
            if not cls._wechat_main_window_class(win32gui.GetClassName(hwnd)):
                return
            if cls._window_process_name(hwnd) not in {"weixin.exe", "wechat.exe"}:
                return
            if not cls._window_is_current_session(hwnd):
                return
            matches.append(hwnd)

        win32gui.EnumWindows(visit, None)
        return matches

    @classmethod
    def _wechat_windows(cls) -> list[int]:
        return cls._enumerate_wechat_windows(visible=True)

    @classmethod
    def _hidden_wechat_windows(cls) -> list[int]:
        return cls._enumerate_wechat_windows(visible=False)

    def _prepare_wechat_window(self) -> tuple[bool, str]:
        """恢复唯一、已存在的隐藏主窗口；绝不启动微信或处理登录。"""

        visible = self._wechat_windows()
        if len(visible) == 1:
            return True, "找到唯一可见微信主窗口"
        if len(visible) > 1:
            return False, f"必须存在唯一可见微信主窗口，当前找到 {len(visible)} 个"

        hidden = self._hidden_wechat_windows()
        if len(hidden) != 1:
            return False, (
                "没有唯一可恢复的微信主窗口"
                f"（可见 {len(visible)} 个，隐藏候选 {len(hidden)} 个）"
            )
        if not self._activate(hidden[0]):
            return False, "唯一隐藏微信主窗口无法安全恢复"
        deadline = time.monotonic() + max(self.stage_timeout, self.delay * 3)
        while time.monotonic() < deadline:
            visible = self._wechat_windows()
            if len(visible) == 1 and visible[0] == hidden[0]:
                return True, "已恢复唯一隐藏微信主窗口"
            if len(visible) > 1:
                return False, f"恢复后出现多个可见微信主窗口（{len(visible)} 个）"
            time.sleep(self.poll_interval)
        return False, "隐藏微信主窗口恢复后仍不可见"

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

    def _find_uia_group_search_match(
        self,
        target: str,
        search_box: tuple[int, int, int, int],
    ) -> tuple[OcrLine | None, str]:
        """Read the uniquely identified group result from WeChat's UIA tree."""

        if not self._window:
            return None, "微信窗口尚未验证"
        try:
            import win32process
            from pywinauto import Desktop

            _, process_id = win32process.GetWindowThreadProcessId(self._window)
            if not process_id:
                return None, "无法确认微信窗口进程"
            items: list[UiaSearchItem] = []
            for window in Desktop(backend="uia").windows(process=process_id, visible_only=True):
                for control in window.descendants(control_type="ListItem"):
                    info = control.element_info
                    automation_id = str(getattr(info, "automation_id", "") or "")
                    if not automation_id.startswith("search_item_"):
                        continue
                    rectangle = info.rectangle
                    items.append(
                        UiaSearchItem(
                            text=str(control.window_text() or ""),
                            automation_id=automation_id,
                            left=float(rectangle.left),
                            top=float(rectangle.top),
                            right=float(rectangle.right),
                            bottom=float(rectangle.bottom),
                        )
                    )
            return _select_uia_group_search_match(items, target, search_box)
        except Exception as exc:
            logger.warning("WeChat UIA search fallback unavailable: %s", exc)
            return None, f"UIA 搜索兜底不可用：{type(exc).__name__}"

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

    def _poll_rounds(self, timeout_seconds: float) -> int:
        return max(1, int(timeout_seconds / self.poll_interval) + 1)

    def _capture_stable_baseline(self):
        previous_composer, previous_chat = self._capture_send_regions()
        attempts = self._poll_rounds(self.stage_timeout)
        for attempt in range(1, attempts + 1):
            time.sleep(self.poll_interval)
            composer, chat = self._capture_send_regions()
            if self._difference_ratio(previous_composer, composer) <= 0.0003:
                return True, composer, chat, attempt
            previous_composer, previous_chat = composer, chat
        return False, previous_composer, previous_chat, attempts

    def _wait_for_staged_change(self, before_composer):
        attempts = self._poll_rounds(self.stage_timeout)
        max_change = 0.0
        for attempt in range(1, attempts + 1):
            composer, _ = self._capture_send_regions()
            change = self._difference_ratio(before_composer, composer)
            max_change = max(max_change, change)
            if change >= 0.0005:
                return composer, change, attempt
            if attempt < attempts:
                time.sleep(self.poll_interval)
        return None, max_change, attempts

    def _wait_for_submission(
        self,
        before_composer,
        staged_composer,
        before_chat,
    ) -> tuple[bool, str, dict[str, object]]:
        attempts = self._poll_rounds(self.submit_timeout)
        last_detail = "尚未观察到提交变化"
        last_metrics: dict[str, float] = {}
        for attempt in range(1, attempts + 1):
            after_composer, after_chat = self._capture_send_regions()
            ok, detail = self._verify_submission(
                before_composer,
                staged_composer,
                after_composer,
                before_chat,
                after_chat,
            )
            last_detail = detail
            last_metrics = self._submission_metrics(
                before_composer,
                staged_composer,
                after_composer,
                before_chat,
                after_chat,
            )
            if ok:
                return True, detail, {
                    "phase": "submit_verified",
                    "submit_attempts": attempt,
                    **{key: round(value, 6) for key, value in last_metrics.items()},
                }
            if attempt < attempts:
                time.sleep(self.poll_interval)
        return False, last_detail, {
            "phase": "submit_unknown",
            "submit_attempts": attempts,
            **{key: round(value, 6) for key, value in last_metrics.items()},
        }

    @classmethod
    def _submission_metrics(
        cls,
        before_composer,
        staged_composer,
        after_composer,
        before_chat,
        after_chat,
    ) -> dict[str, float]:
        staged_change = cls._difference_ratio(before_composer, staged_composer)
        cleared_change = cls._difference_ratio(staged_composer, after_composer)
        returned_toward_empty = cls._difference_ratio(before_composer, after_composer)
        chat_change = cls._difference_ratio(before_chat, after_chat)
        empty_delta_limit = min(max(staged_change * 0.25, 0.0003), 0.0015)
        return {
            "staged_change": staged_change,
            "cleared_change": cleared_change,
            "returned_toward_empty": returned_toward_empty,
            "empty_delta_limit": empty_delta_limit,
            "chat_change": chat_change,
        }

    @classmethod
    def _verify_submission(cls, before_composer, staged_composer, after_composer, before_chat, after_chat) -> tuple[bool, str]:
        metrics = cls._submission_metrics(
            before_composer,
            staged_composer,
            after_composer,
            before_chat,
            after_chat,
        )
        staged_change = metrics["staged_change"]
        cleared_change = metrics["cleared_change"]
        returned_toward_empty = metrics["returned_toward_empty"]
        chat_change = metrics["chat_change"]
        empty_delta_limit = metrics["empty_delta_limit"]
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
            "c": ord("C"),
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

    def _composer_is_empty(self) -> tuple[bool, str]:
        """通过无破坏复制探测输入区；无法证明为空时保守停止。"""
        import win32clipboard
        import win32con

        sentinel = f"GroupBrief-empty-check-{uuid.uuid4().hex}"
        self._set_clipboard_text(sentinel)
        self._hotkey("ctrl", "a")
        self._hotkey("ctrl", "c")
        time.sleep(self.poll_interval)
        try:
            win32clipboard.OpenClipboard()
            formats: list[int] = []
            value = ""
            try:
                current = 0
                while True:
                    current = win32clipboard.EnumClipboardFormats(current)
                    if current == 0:
                        break
                    formats.append(int(current))
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    value = str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or "")
            finally:
                win32clipboard.CloseClipboard()
        except Exception as exc:
            return False, f"无法确认微信输入区为空，已停止发送：{str(exc)[:120]}"
        finally:
            # 重新点击输入区只取消选择，不删除或覆盖任何草稿。
            self._focus_composer()
        draft_media_formats = {
            win32con.CF_BITMAP,
            win32con.CF_DIB,
            getattr(win32con, "CF_DIBV5", 17),
            win32con.CF_HDROP,
        }
        if value == sentinel and not (set(formats) & draft_media_formats):
            return True, "输入区为空"
        return False, "微信输入区可能存在文字或图片草稿，已停止且未清除草稿"

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
                    action.diagnostics,
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
                    text_action.diagnostics,
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
                    image_action.diagnostics,
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
