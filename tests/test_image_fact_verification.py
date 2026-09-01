"""严格图片 OCR 事实校验。"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.ai.strict_prompt_contract import append_strict_image_fact_contract
from app.image.fact_verification import review_image_facts


def _evidence(tmp_path: Path) -> tuple[Path, Path]:
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text(
        "只允许引用：深圳-UI-白白 78.8；群友甲 61；群友乙 66。",
        encoding="utf-8",
    )
    (tmp_path / "messages.json").write_text(
        json.dumps(
            [
                {
                    "group_name": "Eason张UED-4群🤘",
                    "sender_name": "深圳-UI-白白",
                    "content": "今天 78.8万元",
                },
                {"sender_name": "群友甲", "content": "61"},
                {"sender_name": "群友乙", "content": "66"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    image = tmp_path / "daily_image.png"
    Image.new("RGB", (16, 16), "white").save(image)
    return prompt, image


def test_rejects_unverified_numbers_and_factual_phrases(tmp_path):
    prompt, image = _evidence(tmp_path)

    review = review_image_facts(
        prompt,
        image,
        ocr_text="体脂率 12%\n90÷1.72²\nBMI 30.4\n连续下雨 120天",
    )

    assert not review.ok
    assert {"12%", "90", "1.72", "30.4", "120天"}.issubset(
        set(review.unknown_numeric)
    )
    assert review.unknown_text


def test_allows_numbers_and_text_present_in_evidence(tmp_path):
    prompt, image = _evidence(tmp_path)

    review = review_image_facts(
        prompt,
        image,
        ocr_text="深圳-UI-白白 78.8\n群友甲 61\n群友乙 66",
    )

    assert review.ok
    assert review.unknown_numeric == ()
    assert review.unknown_text == ()


def test_allows_ocr_fragments_of_known_numbers_and_ignores_name_garble(tmp_path):
    prompt, image = _evidence(tmp_path)

    review = review_image_facts(
        prompt,
        image,
        ocr_text="写着 78 和 8万 的瓜藤\n罰 一 U 《 一 白 白\n面板 8",
    )

    assert review.ok
    assert review.unknown_numeric == ()
    assert review.unknown_text == ()


def test_numeric_units_do_not_join_across_ocr_lines(tmp_path):
    prompt, image = _evidence(tmp_path)

    review = review_image_facts(
        prompt,
        image,
        ocr_text="0\n米游涩泛二次元同好摸鱼群",
    )

    assert review.ok
    assert review.unknown_numeric == ()


def test_allows_zero_padded_layout_numbers(tmp_path):
    prompt, image = _evidence(tmp_path)

    review = review_image_facts(
        prompt,
        image,
        ocr_text="分镜 01\n成员 05\n日期 09\n体重 078.8万元",
    )

    assert review.ok
    assert review.unknown_numeric == ()


def test_allows_deterministic_header_numbers_and_currency_alias(tmp_path):
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text(
        """【群名称】
Eason张UED-4.1群
【统计时间】
2026-08-30 00:00:00 ~ 2026-08-30 23:59:59
【数据】
138 条消息
20 人发言
【版面1】
38元魔法战神
""",
        encoding="utf-8",
    )
    (tmp_path / "messages.json").write_text(
        json.dumps(
            [{"sender_name": "符号昵称", "content": "38块的游戏"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    image = tmp_path / "daily_image.png"
    Image.new("RGB", (16, 16), "white").save(image)

    review = review_image_facts(
        prompt,
        image,
        ocr_text=(
            "Eason张UED-4.1群\n"
            "2026-08-30 00:00:00 ~ 2026-08-30 23:59:59\n"
            "138 条消息 20 人发言\n38元魔法战神\n符号昵称 0000"
        ),
    )

    assert review.ok
    assert review.unknown_numeric == ()


def test_strict_contract_removes_bmi_display_instructions():
    prompt = """【版面4】
手指一路猜到BMI
话题延伸到身高、BMI和婚后发福。
旁边的计算器连续弹出BMI窗口。
人物说：我的BMI 很标准啊
"""

    strict_prompt = append_strict_image_fact_contract(prompt)

    assert "BMI" not in strict_prompt.upper()
    assert "从手指猜体重" in strict_prompt
    assert "话题延伸到身高和婚后发福" in strict_prompt
    assert "猜体重" in strict_prompt
    assert "我的" not in strict_prompt
