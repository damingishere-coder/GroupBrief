# P1.2 单一调度所有权、退出码与假成功

日期：2026-08-25

## 结论

正式唯一调度 owner 统一为 8766 FastAPI 进程内的 APScheduler。Windows `GroupBriefDaily` 与 `GroupBriefDailySend` 不删除，只禁用保留为可回滚入口；配置和安装脚本会阻止它们在 `fastapi` owner 下重新启用。

```text
整改前
FastAPI APScheduler ─┬─ 00:15 生成+邮件
                    └─ 每分钟微信 send_due
Windows Task ───────┬─ 00:15 daily_auto.py
                    └─ 08:30~09:00 每分钟 send CLI
                          ↓
                    依赖 mutex/claim 碰撞避重

整改后
Alter 管理的 8766
       └─ FastAPI APScheduler（唯一 owner）
              ├─ 00:15 生成+邮件
              ├─ 启动补偿
              └─ 每分钟微信 send_due

Windows 两任务：Disabled（只作回滚，不同时启用）
手动 CLI：保留，输出稳定 outcome 与退出码
```

## 关键问题

- `daily_auto.py` 把 `already_running` 返回为 0，Windows 会误记成成功。
- `run_daily_pipeline.py` 除参数缺失外所有业务分支固定返回 0；failed、held、结果未知都能假成功。
- `run_send_due_job()` 捕获异常后正常返回，APScheduler 会把异常扫描记为正常执行。
- `_generation_status()` 把未识别状态和 `no_groups` 落入 success。
- 生成 partial 后，只要邮件子进程返回 0，整批状态会被覆盖成 success。
- Windows 两阶段安装的第二步失败时，第一步残留，且安装命令仍返回 0。

## 终态与退出码

| outcome | exit code | 运维语义 |
| --- | ---: | --- |
| success | 0 | 本次完整成功或已有可信完成态 |
| failed | 1 | 本次失败 |
| partial | 2 | 部分成功，仍有失败项 |
| blocked | 3 | 结果未知或人工复核，禁止自动推进 |
| already_running | 4 | 本次没有取得执行所有权 |
| not_run | 5 | 没有可执行对象，本次没有实际工作 |

未知业务状态不再默认 success，而是 fail closed 为 failed。APScheduler 没有进程退出码，因此使用同一 outcome；除分钟级正常空扫描 `not_run` 外，非 success 会抛出 `SchedulerOutcomeError`，让调度器记录失败。

## 修改范围

- `app/scheduler/outcome.py`：唯一业务终态、聚合和退出码契约。
- `app/config/settings.py`、`app/main.py`：环境级 `scheduler_owner=fastapi|external|disabled`；只在 fastapi owner 下注册内部调度。
- `app/api/system.py`：只读返回配置 owner 与实际 scheduler active 状态。
- `app/scheduler/daily_v2_job.py`：no_groups/未知状态 fail closed；partial 不再被邮件成功覆盖；记录最近调用 outcome/exit code。
- `app/scheduler/manager.py`、`app/scheduler/send_job.py`：APScheduler 对业务失败不再假成功或吞异常。
- `scripts/daily_auto.py`、`scripts/run_daily_pipeline.py`：打印可机读 `OUTCOME` 并返回稳定退出码。
- `scripts/install_daily_task.py`：owner 冲突时拒绝安装/启用，半安装自动回滚，状态冲突返回 blocked。

## 失败模式

| 场景 | 新行为 |
| --- | --- |
| 两实例争抢生成锁 | 未取得锁的一方 `already_running / exit 4` |
| 全部群生成失败 | `failed / exit 1` |
| 部分群失败、邮件成功 | 整批仍为 `partial / exit 2` |
| scheduler/run 状态损坏或发送结果未知 | `blocked / exit 3` |
| 无启用群 | `not_run / exit 5`，不调用邮件 |
| send_due 没有到点对象 | `not_run`，作为正常分钟扫描，不向 APScheduler 抛错 |
| send_due 返回 failed/held | 调度异常，不能记为成功 |
| Windows 第二个任务安装失败 | 删除刚创建的第一个任务并返回非零 |
| fastapi owner 下安装/启用 Windows 任务 | 操作被阻断 |

## 验证

- P1.2 定向测试：55 项通过（2.42 秒）。
- 隔离工作区完整测试：在新增最后 1 项 API 断言前为 518 项通过、1 条既有 warning；新增断言已包含在上述 55 项定向测试中。
- Python `compileall`：通过。
- 前端生产构建：通过（4596 modules transformed，约 5.65 秒）。
- `git diff --check`：通过。
- 独立完整测试随后两次被全局生成 mutex 阻断；进程取证确认锁持有者是另一个正在执行 23–28 号群组 Prompt 重建并调用摘要模型的真实任务，不是 P1.2 回归，也未强制终止。待该写入任务自然结束后补做一次无并发完整复验。
- 2026-08-25 10:10 正式 owner 切换完成：`GroupBriefDaily` 与 `GroupBriefDailySend` 均为 `Disabled`，导出的任务 XML `Enabled=false`。
- 切换后 `scripts/install_daily_task.py status` 返回 `scheduler_owner=fastapi`、两任务 disabled、`outcome=success / exit 0`。
- 切换后 Alter 管理的 8766 仍正常运行，`/api/system/status` 返回 HTTP 200，下一生成时间为 2026-08-26 00:15。
- 由于同一工作区另有未提交的 Prompt 生产代码正在执行，本轮没有冒险重启 8766；当前内置 APScheduler 原本已在运行，单一 owner 切换立即生效，新退出码/状态接口将在安全重启后加载。
- 未触发真实 AI、邮件或微信发送。

## 回滚

回滚必须按顺序进行，不能先启用 Windows 任务：

1. 将 8766 的 `SCHEDULER_OWNER` 切为 `external` 并重启，确认 API 返回 `scheduler_active=false`。
2. 再启用 `GroupBriefDaily` 和 `GroupBriefDailySend`。
3. 如需回滚代码，单独 revert 本次提交；没有数据库 Schema 变更。

## 非目标

- 不在本轮重写 V1 运行状态；V1 写路径在 P1.5 冻结和退役。
- 不修改 Provider、Mock、AI/邮件幂等策略。
- 不新增队列、分布式锁或外部调度平台。
