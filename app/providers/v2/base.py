"""V2 预留接口。

V1 不实现，仅保留接口定义，保证 V2 可直接接管。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class GeneratedImage:
    path: Path
    status: str = "generated"


class ImageGenerationProvider:
    """V2：读取 image_prompt.txt 生成海报图片。"""

    def generate(self, prompt_file: Path) -> GeneratedImage:
        raise NotImplementedError("V2 支持")


class WeChatDeliveryProvider:
    """V2：向微信群发送文字与海报图片。"""

    def send_text(self, group, text: str) -> bool:
        raise NotImplementedError("V2 支持")

    def send_image(self, group, image_path: Path) -> bool:
        raise NotImplementedError("V2 支持")
