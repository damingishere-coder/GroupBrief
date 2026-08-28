"""V2 群级统计周期解析器。"""

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
    rule: str = "daily_previous_day"
    covered_dates: list[date] | None = None

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
        schedule_rule: str = "daily_previous_day",
    ) -> PeriodWindow:
        tz = ZoneInfo(timezone)
        today = run_date or datetime.now(tz).date()
        weekday = today.weekday()

        if schedule_rule == "daily_previous_day":
            targets = [today - timedelta(days=1)]
            should_run = True
        elif schedule_rule == "weekday_default":
            if weekday >= 5:
                targets = [today - timedelta(days=1)]
                should_run = False
            elif weekday == 0:
                targets = [today - timedelta(days=offset) for offset in (3, 2, 1)]
            else:
                targets = [today - timedelta(days=1)]
            should_run = weekday < 5
        else:
            raise NotImplementedError(f"暂不支持的统计周期规则：{schedule_rule}")

        return PeriodWindow(
            run_date=today,
            period_start=datetime.combine(min(targets), time.min),
            period_end=datetime.combine(max(targets), _END_OF_DAY),
            should_run=should_run,
            weekday=weekday,
            rule=schedule_rule,
            covered_dates=targets,
        )

    def format_dt(self, dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
