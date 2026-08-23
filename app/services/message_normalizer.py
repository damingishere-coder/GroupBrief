"""消息标准化：RawMessage → NormalizedMessage。

- 过滤系统消息（入群/退群/撤回/群名变化等）
- 统一时间格式
- 提取可用于 AI 的文本（链接保留标题/内容，图片等标记为媒体）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.providers.history.base import RawMessage

# 计入排行榜的消息类型（文档 §6.1）
COUNTABLE_TYPES = {
    "text",
    "image",
    "emoji",
    "voice",
    "video",
    "file",
    "link",
    "quote",
    "red_packet",
    "chat_history",
    "transfer",
    "other",
}

# 不计入的系统内容关键词（微信系统生成，文档 §6.2）
SYSTEM_KEYWORDS = [
    "加入了群聊",
    "邀请你加入了群聊",
    "退出群聊",
    "被移出群聊",
    "撤回了一条消息",
    "修改群名",
    "将群聊名称修改为",
    "开启了全员禁言",
    "关闭了全员禁言",
]


@dataclass
class NormalizedMessage:
    group_id: str
    group_name: str
    sender_id: str
    sender_name: str
    timestamp: datetime
    message_type: str
    content: str
    source: str
    content_hash: str
    countable: bool = True
    ai_text: str = ""  # 供 AI 使用的文本

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "timestamp": self.timestamp.isoformat(),
            "message_type": self.message_type,
            "content": self.content,
            "source": self.source,
            "content_hash": self.content_hash,
            "countable": self.countable,
            "ai_text": self.ai_text,
        }


class MessageNormalizer:
    @staticmethod
    def is_system_message(message: RawMessage) -> bool:
        if message.message_type == "system":
            return True
        if any(kw in message.content for kw in SYSTEM_KEYWORDS):
            return True
        return False

    @staticmethod
    def is_countable(message: RawMessage) -> bool:
        if MessageNormalizer.is_system_message(message):
            return False
        return message.message_type in COUNTABLE_TYPES

    @staticmethod
    def to_ai_text(message: RawMessage) -> str:
        """提取适合交给 LLM 的文本。"""
        content = message.content or ""
        prefix = {
            "image": "[图片]",
            "emoji": "[表情]",
            "voice": "[语音]",
            "video": "[视频]",
            "file": "[文件]",
            "red_packet": "[红包]",
            "chat_history": "[聊天记录]",
            "transfer": "[转账]",
        }.get(message.message_type, "")
        # 图片/文件内容可能是本地路径、XML、base64 或导出器内部载荷；AI 只看占位符。
        if message.message_type in {"image", "file"}:
            return prefix
        text = content if content else prefix
        if prefix and not text.startswith("["):
            text = f"{prefix} {text}"
        return text

    @staticmethod
    def normalize(message: RawMessage) -> NormalizedMessage:
        countable = MessageNormalizer.is_countable(message)
        return NormalizedMessage(
            group_id=message.group_id,
            group_name=message.group_name,
            sender_id=message.sender_id or message.sender_name,
            sender_name=message.sender_name or "(未知)",
            timestamp=message.timestamp,
            message_type=message.message_type,
            content=message.content,
            source=message.source,
            content_hash=message.content_hash,
            countable=countable,
            ai_text=MessageNormalizer.to_ai_text(message),
        )


def normalize_messages(messages: list[RawMessage]) -> list[NormalizedMessage]:
    result = [MessageNormalizer.normalize(m) for m in messages]
    result.sort(key=lambda m: m.timestamp)
    return result
