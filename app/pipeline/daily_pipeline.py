"""V2 每日全流程流水线骨架（P7 实现）。

生成阶段（默认 08:00）：取数 → messages.json → ranking.json/txt → image_prompt.txt
                              → Codex 串行生图 → daily_image.png → READY_TO_SEND
发送阶段（每群 send_time）：发排行榜文字 → 发图片 → SENT

每个群独立状态；某群失败不阻塞其他群；生图全局单队列串行。
"""

from __future__ import annotations


class DailyPipeline:
    """组装 P1-P6 的完整流水线。P7 实现。"""

    def generate_all(self, run_date=None, group_ids=None, force: bool = False) -> list[dict]:
        """生成阶段：处理一个或多个群，返回每群状态。"""
        raise NotImplementedError

    def send_due(self, now=None) -> list[dict]:
        """发送阶段：处理到点群，返回每群发送结果。"""
        raise NotImplementedError
