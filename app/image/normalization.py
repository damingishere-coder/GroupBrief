"""群报转为不透明图片时，先合成透明层，不能直接丢弃 alpha。"""

from PIL import Image


def opaque_report_image(image: Image.Image) -> Image.Image:
    """在白底上合成 RGBA/LA/调色板透明图，避免隐藏 RGB 变成黑色污迹。"""
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        return Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    return image.convert("RGB")
