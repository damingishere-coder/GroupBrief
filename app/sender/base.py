"""V2 微信发送抽象接口（P6 实现）。

GroupBrief 业务层不直接依赖任何微信自动化项目内部实现。
当前目标 Provider：Windows 键盘、剪贴板与 OCR 驱动。
更换方案时只替换 Provider 实现。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendResult:
    success: bool
    detail: str = ""
    sent_at: str = ""  # ISO 时间
    # submitted 表示已执行 Enter/Provider 提交动作；success 仅在 Provider
    # 能给出相应验证时为 True。原生微信使用 ui_observed，不宣称服务器回执。
    submitted: bool = False
    verification_level: str = ""
    outcome_unknown: bool = False


class WechatSender:
    """V2 统一微信发送接口。"""

    name: str = "base"

    def health_check(self) -> tuple[bool, str]:
        raise NotImplementedError

    def send_text(self, target: str, text: str) -> SendResult:
        """向指定目标（群名）发送文字。"""
        raise NotImplementedError

    def send_image(self, target: str, image_path) -> SendResult:
        """向指定目标（群名）发送本地图片。"""
        raise NotImplementedError

    def send_bundle(self, target: str, text: str, image_path=None) -> tuple[SendResult, SendResult | None]:
        """在一个发送会话中发送文字和可选图片；旧实现默认按顺序调用。"""
        text_result = self.send_text(target, text)
        if not text_result.success or image_path is None:
            return text_result, None
        return text_result, self.send_image(target, image_path)
