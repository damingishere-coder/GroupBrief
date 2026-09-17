"""日报与历史重画共用的文字区视觉约束。"""

import re


# 仅用于迁移已保存 Prompt 中的完整旧规则，不模糊删除用户文案。
LEGACY_IMAGE_READABILITY_RULES = (
    "顶部群名称、统计时间、主副标题，以及底部总结、消息数和发言人数，"
    "必须使用不透明、平整干净的浅色背景和完整锐利的深色文字，保持高对比度。"
    "文字区禁止黑色模糊光晕、暗角、烟雾、渐变阴影、涂抹、擦除或半透明蒙层；"
    "不要用纹理或大投影覆盖文字。装饰只能放在文字区外，手机端必须清晰可读。"
)

IMAGE_READABILITY_RULES = (
    "群名称、统计时间、主标题、副标题、底部总结和统计数据，必须与漫画画面一起由 ImageGen 原生生成。"
    "从构图开始就把副标题和底部总结设计为画面的一部分：根据本图风格选择手绘题签、纸条、"
    "场景标牌或与插画相连的文字构图，文字载体的色彩、线条和透视与周围漫画一致；"
    "不要默认排成独立的纯文字横条，不要留下空白区域等待后贴文字。"
    "保留所有给定文字原文，不增加新的总结分镜、人物、事实或对白。"
    "整张图包括文字载体均使用不透明背景；字形完整清晰、文字与局部背景高对比，"
    "手机端可读，不遮挡、不裁切、不用过小字号。"
    "装饰可以与文字载体自然融合，但不得覆盖笔画；禁止黑色模糊光晕、暗角、烟雾、"
    "涂抹、擦除、半透明蒙层或大投影遮挡文字。"
    "不得使用 Python、Pillow、SVG 或其他程序在生成图片上补字、贴字或覆盖标题。"
)

_VISUAL_SECTION = re.compile(
    r"(?ms)(^【整体视觉】[^\S\r\n]*\r?\n)(.*?)(?=^【[^\r\n】]+】[^\S\r\n]*\r?$|\Z)"
)


def apply_image_visual_rules(prompt: str) -> str:
    """更新视觉区完整规则段，保留其他区块；重复调用不叠加规则。"""
    section = _VISUAL_SECTION.search(prompt)
    body = section.group(2) if section else prompt
    for rule in (LEGACY_IMAGE_READABILITY_RULES, IMAGE_READABILITY_RULES):
        body = re.sub(r"(?m)^[ \t]*" + re.escape(rule) + r"[ \t]*(?:\r?\n|$)", "", body)
    body = re.sub(r"(?:\r?\n){3,}", "\n\n", body).strip()
    if section:
        replacement = IMAGE_READABILITY_RULES + ("\n\n" + body if body else "") + "\n\n"
        return prompt[:section.start(2)] + replacement + prompt[section.end(2):]
    # 兼容没有固定区块的历史 Prompt，不改写其正文。
    return (body + "\n\n" if body else "") + IMAGE_READABILITY_RULES
