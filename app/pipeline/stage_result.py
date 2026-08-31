"""Pipeline 阶段间的显式继续/终止协议。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar


PayloadT = TypeVar("PayloadT")


class StageDisposition(StrEnum):
    CONTINUE = "continue"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class StageResult(Generic[PayloadT]):
    """阶段要么携带下一阶段输入，要么携带对外终止结果。"""

    disposition: StageDisposition
    value: PayloadT | None = None
    response: dict | None = None

    @classmethod
    def proceed(cls, value: PayloadT) -> "StageResult[PayloadT]":
        return cls(StageDisposition.CONTINUE, value=value)

    @classmethod
    def stop(cls, response: dict) -> "StageResult[PayloadT]":
        return cls(StageDisposition.TERMINAL, response=response)

    @property
    def is_terminal(self) -> bool:
        return self.disposition is StageDisposition.TERMINAL

    def terminal_response(self) -> dict:
        if not self.is_terminal or self.response is None:
            raise RuntimeError("继续阶段没有终止响应")
        return self.response

    def next_value(self) -> PayloadT:
        if self.is_terminal or self.value is None:
            raise RuntimeError("终止阶段没有下一阶段输入")
        return self.value
