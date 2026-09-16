"""首尾暗色遮挡回归：合成像素夹具不包含任何真实群聊。"""

import json

import pytest
from PIL import Image, ImageDraw

from app.ai.image_readability import IMAGE_READABILITY_RULES
from app.ai.poster_copy import _overall_visual
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import verify_image_contract
from app.image.readability import review_image_readability
from app.image.normalization import opaque_report_image


@pytest.mark.parametrize("region", ["header", "footer"])
def test_dark_low_contrast_text_band_blocks_report(tmp_path, region):
    image = Image.new("RGB", (512, 768), "white")
    draw = ImageDraw.Draw(image)
    y = 0 if region == "header" else 706
    draw.rectangle((75, y, 440, y + 78), fill=(45, 55, 70))
    draw.text((90, y + 10), "unreadable title", fill=(25, 30, 40))
    path = tmp_path / "daily_image.png"
    image.save(path)
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("【群名称】\n测试群\n【统计时间】\n2026-09-15", encoding="utf-8")

    ok, detail = verify_image_contract(prompt, path)

    assert not ok
    assert "暗色低对比" in detail
    review = json.loads((tmp_path / "image_readability_review.json").read_text(encoding="utf-8"))
    assert not review["ok"]
    assert len(review["image_sha256"]) == 64


def test_clear_text_and_dark_comic_body_are_accepted(tmp_path):
    image = Image.new("RGB", (512, 768), "#faf0dc")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 85, 511, 700), fill="black")
    for x in range(90, 420, 25):
        draw.rectangle((x, 15, x + 9, 45), fill="black")
        draw.rectangle((x, 730, x + 7, 750), fill="black")
    path = tmp_path / "clear.png"
    image.save(path)
    assert review_image_readability(path)["ok"]


def test_bright_strokes_on_dark_background_are_not_dark_occlusion(tmp_path):
    image = Image.new("RGB", (512, 768), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((70, 0, 440, 77), fill="#102040")
    for x in range(70, 441, 6):
        draw.line((x, 0, x, 77), fill="white", width=2)
    path = tmp_path / "contrast.png"
    image.save(path)
    assert review_image_readability(path)["ok"]


def test_non_report_image_keeps_generic_validation(tmp_path):
    path = tmp_path / "generic.png"
    Image.new("RGB", (512, 768), "black").save(path)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("a black background", encoding="utf-8")
    assert verify_image_contract(prompt, path)[0]


def test_fresh_and_historical_generation_share_readability_rules():
    assert IMAGE_READABILITY_RULES in _overall_visual("", explicit_style=False)
    assert IMAGE_READABILITY_RULES in CodexImageGenerator._attempt_prompt("历史 Prompt", "test-job-123")


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_transparency_is_composited_before_rgb_promotion(tmp_path, mode):
    image = Image.new("RGBA", (512, 768), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((150, 20, 250, 50), fill=(20, 30, 40, 255))
    if mode == "LA":
        image = image.convert("LA")
    elif mode == "P":
        image = image.convert("P", palette=Image.Palette.ADAPTIVE)
    source = tmp_path / "source.png"
    target = tmp_path / "daily_image.png"
    image.save(source)
    original = source.read_bytes()
    CodexImageGenerator._promote_valid_image(source, target)
    with Image.open(target) as final:
        assert final.mode == "RGB"
        assert final.getpixel((10, 10)) == (255, 255, 255)
        assert max(final.getpixel((200, 30))) < 50
    assert source.read_bytes() == original
    assert review_image_readability(source)["ok"]
    assert review_image_readability(target)["ok"]


def test_semitransparent_shadow_and_opaque_art_preserve_expected_pixels():
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 128))
    image.putpixel((1, 0), (20, 80, 150, 255))
    final = opaque_report_image(image)
    assert final.getpixel((0, 0)) == (127, 127, 127)
    assert final.getpixel((1, 0)) == (20, 80, 150)
