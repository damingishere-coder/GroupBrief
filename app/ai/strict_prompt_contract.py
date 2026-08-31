"""严格群报图片的可见文案与事实边界。"""

from __future__ import annotations

import re


STRICT_IMAGE_FACT_MARKER = "【严格事实边界】"

STRICT_IMAGE_FACT_CONTRACT = f"""

{STRICT_IMAGE_FACT_MARKER}
这是一项硬性验收合同，优先级高于任何视觉丰富化建议：
1. 图片中所有可见文字只能来自本 Prompt 已明确列出的群名、标题、话题标题、人物昵称、真实聊天气泡、事实说明、统计数据和底部总结。
2. 禁止补充任何未逐字列出的数字、单位、百分比、金额、体重、身高、健康计算公式、温度、天气持续天数、日期、时间、饮食建议或健康结论。
3. 禁止把“体脂率低”“健康指标很标准”“雨天包年”等口语扩写成具体百分比、计算公式、温度或天数。
4. 禁止自行改写计划或建议；例如原文没有“少油少盐”时不得生成该文字。
5. 视觉笑点只能通过人物动作、表情、道具形状和无文字装饰表达，不得添加新的事实性标签、App 数值、仪表盘或数据卡片。
6. 无法确认的文字宁可不画；不要用占位符、乱码、伪汉字或装饰性英文填充。
""".strip("\n")


def sanitize_strict_image_prompt(prompt: str) -> str:
    """移除 Eason 合同明确禁用的 BMI 展示，同时保留有证据的猜体重话题。"""
    safe_lines: list[str] = []
    for raw_line in str(prompt or "").splitlines():
        line = raw_line
        line = re.sub(r"手指一路猜到\s*BMI", "从手指猜体重", line, flags=re.IGNORECASE)
        line = re.sub(r"身高[、,，]\s*BMI\s*(?:和|与|及)", "身高和", line, flags=re.IGNORECASE)
        line = re.sub(
            r"(?:旁边的?)?计算器[^。\n]{0,16}弹出\s*BMI\s*窗口",
            "旁边连续弹出“猜体重”的聊天气泡",
            line,
            flags=re.IGNORECASE,
        )
        # 用户要求图片不出现 BMI；无法安全改写的整行直接删除。
        if re.search(r"BMI", line, flags=re.IGNORECASE):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines)


def append_strict_image_fact_contract(prompt: str) -> str:
    text = sanitize_strict_image_prompt(prompt).rstrip()
    if STRICT_IMAGE_FACT_MARKER in text:
        return text + "\n"
    return f"{text}\n\n{STRICT_IMAGE_FACT_CONTRACT}\n"
