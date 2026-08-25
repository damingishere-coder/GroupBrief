"""GroupBrief V2 全局约定：状态机、错误类型、run.json Schema、输出文件命名。

V2 各模块（pipeline / image / sender / data_sources）统一引用本模块，
避免字符串散落在业务代码中。本文件为接口约定，P0 建立，后续轮次复用。
"""

from __future__ import annotations

# ---------- V2 运行状态机 ----------
PENDING = "PENDING"  # 任务已创建，待取数
DATA_READY = "DATA_READY"  # 数据已读取并落盘 messages.json
RANKING_READY = "RANKING_READY"  # 排行已生成（ranking.json / ranking.txt）
PROMPT_READY = "PROMPT_READY"  # 生图 Prompt 已生成（image_prompt.txt）
IMAGE_READY = "IMAGE_READY"  # 图片已生成并落盘（daily_image.png）
READY_TO_SEND = "READY_TO_SEND"  # 内容齐备，等待发送
SENT = "SENT"  # 已发送完成
FAILED = "FAILED"  # 失败（failed_stage 记录失败阶段）
CORRUPT = "CORRUPT"  # 状态文件存在但损坏；只读隔离，禁止自动推进

STATUS_FLOW = (
    PENDING,
    DATA_READY,
    RANKING_READY,
    PROMPT_READY,
    IMAGE_READY,
    READY_TO_SEND,
    SENT,
    FAILED,
    CORRUPT,
)

# 是否允许跳过生图直接进入发送就绪（image_enabled=false 时 PROMPT_READY → READY_TO_SEND）
# 已发送状态不可再发送
TERMINAL_STATUSES = frozenset({SENT, FAILED, CORRUPT})


# ---------- V2 错误类型 ----------
WECHAT_DATA_UNAVAILABLE = "WECHAT_DATA_UNAVAILABLE"
GROUP_NOT_FOUND = "GROUP_NOT_FOUND"
MESSAGE_FETCH_FAILED = "MESSAGE_FETCH_FAILED"
MESSAGE_SNAPSHOT_INVALID = "MESSAGE_SNAPSHOT_INVALID"
RANKING_FAILED = "RANKING_FAILED"
DEEPSEEK_FAILED = "DEEPSEEK_FAILED"
PROMPT_FAILED = "PROMPT_FAILED"
IMAGE_GENERATION_FAILED = "IMAGE_GENERATION_FAILED"
IMAGE_FILE_MISSING = "IMAGE_FILE_MISSING"
WECHAT_OFFLINE = "WECHAT_OFFLINE"
SEND_TEXT_FAILED = "SEND_TEXT_FAILED"
SEND_IMAGE_FAILED = "SEND_IMAGE_FAILED"
RUN_STATE_CORRUPT = "RUN_STATE_CORRUPT"
SCHEDULER_STATE_CORRUPT = "SCHEDULER_STATE_CORRUPT"

# ---------- V2 输出文件命名（output/{群}/{日期}/） ----------
FILE_MESSAGES = "messages.json"
FILE_RANKING_JSON = "ranking.json"
FILE_RANKING_TXT = "ranking.txt"
FILE_PROMPT = "image_prompt.txt"
FILE_PROMPT_ORIGINAL = "image_prompt.original.txt"
FILE_IMAGE = "daily_image.png"
FILE_IMAGE_PREVIOUS = "daily_image.previous.png"
FILE_IMAGE_REGENERATING = "daily_image.regenerating.png"
FILE_RUN = "run.json"


def is_terminal(status: str) -> bool:
    """任务是否已处于终态（不可再推进）。"""
    return status in TERMINAL_STATUSES


def is_ready_to_send(status: str) -> bool:
    """内容是否已齐备（生图开关关闭时 PROMPT_READY 即视为就绪）。"""
    return status in (IMAGE_READY, READY_TO_SEND, SENT)
