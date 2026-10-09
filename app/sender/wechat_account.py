"""固定头像账号绑定。确定性比较，任何候选不可读或歧义均失败关闭。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
import uuid
from datetime import datetime, timezone

from PIL import Image, ImageChops, ImageDraw, ImageStat

BINDING_KEY = "wechat_sender_account_binding"
HASH_LIMIT = 4
PIXEL_LIMIT = 0.045
_candidates: dict[str, dict] = {}


def normalized_avatar(image: Image.Image) -> Image.Image:
    if image.width < 24 or image.height < 24:
        raise ValueError("头像图像尺寸不足")
    result = image.convert("RGB").resize((64, 64), Image.Resampling.LANCZOS)
    # 圆角、焦点边框和外侧底色不参与账号判断。
    center = result.crop((8, 8, 56, 56))
    if sum(ImageStat.Stat(center).stddev) < 12:
        raise ValueError("头像为空白或尚未加载")
    return result


def avatar_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    normalized_avatar(image).save(output, format="PNG")
    return output.getvalue()


def avatar_preview(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _hash(image: Image.Image) -> int:
    resized = image.crop((8, 8, 56, 56)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = [resized.getpixel((col, row)) for row in range(8) for col in range(9)]
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (pixels[row * 9 + col] > pixels[row * 9 + col + 1])
    return value


def compare_avatars(reference: Image.Image, observed: Image.Image) -> dict:
    first, second = normalized_avatar(reference), normalized_avatar(observed)
    mask = Image.new("L", (64, 64))
    ImageDraw.Draw(mask).ellipse((7, 7, 56, 56), fill=255)
    difference = ImageStat.Stat(ImageChops.difference(first, second), mask)
    error = sum(difference.mean) / (3 * 255)
    distance = (_hash(first) ^ _hash(second)).bit_count()
    return {"matched": distance <= HASH_LIMIT and error <= PIXEL_LIMIT,
            "hash_distance": distance, "pixel_error": round(error, 6)}


def reference_path(settings, digest: str):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("标准头像指纹无效")
    return settings.db_path.parent / "wechat-account" / f"{digest}.png"


def load_reference(settings) -> tuple[dict, Image.Image]:
    raw = settings.wechat_sender_account_binding
    if not raw:
        raise ValueError("尚未绑定微信发送账号，请在设置页确认标准头像")
    try:
        binding = json.loads(raw)
        data = reference_path(settings, binding["avatar_sha256"]).read_bytes()
        if binding.get("version") != 1 or hashlib.sha256(data).hexdigest() != binding["avatar_sha256"]:
            raise ValueError("标准头像校验失败")
        return binding, normalized_avatar(Image.open(io.BytesIO(data)))
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("标准头像丢失或绑定记录损坏，请重新绑定") from exc


def binding_status(settings) -> dict:
    try:
        binding, image = load_reference(settings)
        return {"bound": True, "account_name": binding["account_name"],
                "avatar_sha256": binding["avatar_sha256"], "avatar": avatar_preview(avatar_bytes(image)),
                "bound_at": binding["bound_at"], "detail": "已保存标准头像"}
    except ValueError as exc:
        return {"bound": False, "detail": str(exc)}


def scan_windows(driver, *, restore: bool) -> list[dict]:
    if not driver._desktop_unlocked():
        raise ValueError("Windows 桌面已锁定，未操作微信窗口")
    handles = list(dict.fromkeys(driver._wechat_windows() + driver._hidden_wechat_windows()))
    result = []
    for hwnd in handles:
        item = {"hwnd": hwnd, "ok": False}
        try:
            identity = driver._account_window_identity(hwnd)
            image = driver._capture_account_avatar(hwnd, restore=restore)
            # 两次读取避免绑定或匹配正在加载、切换的头像。
            again = driver._capture_account_avatar(hwnd, restore=False)
            if identity != driver._account_window_identity(hwnd) or not compare_avatars(image, again)["matched"]:
                raise ValueError("窗口或头像正在变化，请稍后重试")
            data = avatar_bytes(again)
            item.update(ok=True, identity=identity, image=again, data=data,
                        fingerprint=hashlib.sha256(data).hexdigest())
        except Exception as exc:
            item["detail"] = f"头像读取失败：{exc}"
        result.append(item)
    return result


def inspect_account(driver, *, restore: bool = False) -> dict:
    try:
        binding, reference = load_reference(driver.settings)
        windows = scan_windows(driver, restore=restore)
        matches, unreadable, diagnostics = [], [], []
        for window in windows:
            detail = {"hwnd": window["hwnd"], "ok": window["ok"]}
            if not window["ok"]:
                unreadable.append(window["hwnd"])
                detail["detail"] = window["detail"]
            else:
                comparison = compare_avatars(reference, window["image"])
                detail.update(comparison)
                if comparison["matched"]:
                    matches.append(window)
            diagnostics.append(detail)
        ok = len(matches) == 1 and not unreadable
        if unreadable:
            message = "存在无法读取头像的微信窗口，已停止账号选择"
        elif len(matches) != 1:
            message = f"标准头像必须唯一匹配，当前匹配 {len(matches)} 个微信窗口"
        else:
            message = f"已唯一核验微信发送账号：{binding['account_name']}"
        return {"ok": ok, "detail": message, "account_name": binding["account_name"],
                "avatar_sha256": binding["avatar_sha256"], "match_count": len(matches),
                "window_count": len(windows), "windows": diagnostics,
                "identity": matches[0]["identity"] if ok else None}
    except Exception as exc:
        return {"ok": False, "detail": str(exc), "match_count": 0}


def candidate_previews(driver) -> dict:
    now = time.monotonic()
    for token in list(_candidates):
        if now - _candidates[token]["created"] > 300:
            del _candidates[token]
    if len(_candidates) > 32:
        _candidates.clear()
    items = []
    for index, window in enumerate(scan_windows(driver, restore=True), 1):
        item = {"label": f"微信窗口 {index}", "ok": window["ok"]}
        if window["ok"]:
            token = uuid.uuid4().hex
            _candidates[token] = {**window, "created": now}
            item.update(candidate_id=token, avatar=avatar_preview(window["data"]))
        else:
            item["detail"] = window["detail"]
        items.append(item)
    return {"candidates": items}


def prepare_binding(driver, candidate_id: str, account_name: str) -> str:
    candidate = _candidates.get(candidate_id)
    if not candidate or time.monotonic() - candidate["created"] > 300:
        raise ValueError("头像预览已过期，请重新扫描并选择")
    if not driver._desktop_unlocked():
        raise ValueError("桌面已锁定，无法绑定")
    hwnd = candidate["hwnd"]
    if hwnd not in driver._wechat_windows() + driver._hidden_wechat_windows():
        raise ValueError("预览对应窗口已关闭，请重新扫描")
    if driver._account_window_identity(hwnd) != candidate["identity"]:
        raise ValueError("预览对应窗口已变化，请重新扫描")
    current = driver._capture_account_avatar(hwnd, restore=True)
    if not compare_avatars(candidate["image"], current)["matched"]:
        raise ValueError("预览后头像已变化，请重新扫描并确认")
    data = candidate["data"]
    digest = candidate["fingerprint"]
    path = reference_path(driver.settings, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("已保存的标准头像文件损坏")
    else:
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
    return json.dumps({"version": 1, "account_name": account_name.strip(),
                       "avatar_sha256": digest, "bound_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)
