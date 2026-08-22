"""V2 每日前一自然日统计周期解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# 统计终点：精确到秒的 23:59:59（V2 输出格式要求，不含微秒）
_END_OF_DAY = time(23, 59, 59)


@dataclass
class PeriodWindow:
    """V2 统计周期结果。"""

    run_date: date  # 执行日
    period_start: datetime  # 统计起点（含）
    period_end: datetime  # 统计终点（含）
    should_run: bool  # 今天是否生成
    weekday: int  # 0=周一 ... 6=周日
    rule: str = "weekday_default"
    covered_dates: list[date] | None = None  # 覆盖的自然日（每日固定一天）

    def period_start_str(self) -> str:
        return self.period_start.strftime("%Y-%m-%d %H:%M:%S")

    def period_end_str(self) -> str:
        return self.period_end.strftime("%Y-%m-%d %H:%M:%S")


class PeriodResolver:
    """根据运行日期与群配置的 schedule_rule 计算统计周期。"""

    def resolve(
        self,
        run_date: date | None = None,
        timezone: str = "Asia/Shanghai",
        schedule_rule: str = "weekday_default",
    ) -> PeriodWindow:
        tz = ZoneInfo(timezone)
        today = run_date or datetime.now(tz).date()
        weekday = today.weekday()

        if schedule_rule != "weekday_default":
            # 预留扩展：其他周期规则在此注册
            raise NotImplementedError(f"暂不支持的统计周期规则：{schedule_rule}")

        target = today - timedelta(days=1)
        return PeriodWindow(
            run_date=today,
            period_start=datetime.combine(target, time.min),
            period_end=datetime.combine(target, _END_OF_DAY),
            should_run=True,
            weekday=weekday,
            covered_dates=[target],
        )

    def format_dt(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
