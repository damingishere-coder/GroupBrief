"""V2 微信发送抽象接口（P6 实现）。

GroupBrief 业务层不直接依赖任何微信自动化项目内部实现。
当前目标 Provider：Windows 微信 UI Automation（wechat-automation-api）。
更换方案时只替换 Provider 实现。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendResult:
    success: bool
    detail: str = ""
    sent_at: str = ""  # ISO 时间


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
