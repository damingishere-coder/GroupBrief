# P1.5 V1 双轨冻结与逐步退役

日期：2026-08-25

## 结论

旧 V1 数据库流水线现在默认处于 `read_only`：历史数据库、历史文件和 GET 查询继续保留，但旧生成、旧 Prompt 写入、旧邮件发送和旧 scheduler job 均不能再与正式 V2 Pipeline 形成双写。短期回滚兼容只能通过环境级 `LEGACY_V1_WRITE_MODE=maintenance` 显式开启，设置 UI 和数据库值不能开启它。

```text
正式路径
  8766 APScheduler → V2 DailyPipeline → V2 run.json/output → V2 邮件/微信

旧 V1 兼容链
  GET 历史/文件 → 保留只读
  POST/PUT 生成/编辑/邮件 → HTTP 410
  旧 generate/email job → blocked / exit 3
  ReportService.generate / EmailService.send → 中央策略再次阻断
```

## 侦察证据

- 当前前端只使用 `/api/v2/*`，没有发现 `/api/reports/*` 或 `/api/email/*` 写调用。
- 当前 APScheduler 只注册 `daily_v2_generate_email` 与 `send_wechat_due`，没有注册旧 generate/email job。
- Windows 正式任务也使用 `daily_auto.py` 和 `run_daily_pipeline.py`，均属于 V2 链路。
- V1 与 V2 都会调用 AI/SMTP，但分别写数据库状态和 `run.json` 状态；共用 mutex/邮件指纹只能减小并发，不能让两套状态成为同一个真相。
- `legacy_cli` 是 V2 可选微信 Sender，不是独立 V1 Pipeline，本轮不误删。

## 默认冻结的写入口

| 入口 | 新行为 | 替代入口 |
| --- | --- | --- |
| `POST /api/reports/generate` | HTTP 410 / `LEGACY_V1_WRITE_BLOCKED` | `POST /api/v2/pipeline/generate` |
| `PUT /api/reports/{id}/prompt` | HTTP 410 / `LEGACY_V1_WRITE_BLOCKED` | V2 run Prompt API |
| `POST /api/email/send` | HTTP 410 / `LEGACY_V1_WRITE_BLOCKED` | V2 每日任务或 V2 邮件脚本 |
| `run_generate_job()` | blocked / exit 3 | `daily_v2_generate_email` |
| `run_email_job()` | blocked / exit 3 | `daily_v2_generate_email` |
| `ReportService.generate()` | 外部调用和数据库写入前抛冻结异常 | `DailyPipeline` |
| `EmailService.send()` | 构建邮件和 SMTP 前抛冻结异常 | V2 per-group 邮件交付 |

API 层与 Service 层同时检查：API 给旧客户端稳定的 410 契约，Service guard 防止人工 import 或遗留脚本绕过 API。

## 保留的只读兼容面

- `GET /api/reports/latest`
- `GET /api/runs`、`GET /api/runs/{id}`
- `GET /api/files/*`
- `GET /api/email/preview`
- `GET /api/system/stats`
- V1 SQLite 表、历史 Report/Run/GroupRun 与旧输出目录

这些 router/operation 已在 OpenAPI 标记 deprecated，但不会在本轮删除。共享的 `/api/groups/*`、`/api/settings/*` 仍是 V2 正式配置入口，不属于冻结对象。

## 配置与防绕过

- 默认：`LEGACY_V1_WRITE_MODE=read_only`。
- 临时兼容：`LEGACY_V1_WRITE_MODE=maintenance`，每次旧写入记录 warning。
- `/api/system/status` 返回当前模式和 `legacy_v1_writes_active`。
- `legacy_v1_write_mode` 不在设置 API 可编辑键中。
- `Settings.apply_runtime_values()` 明确拒绝从数据库应用 `legacy_v1_write_mode`；同时统一保护既有的 `allow_test_providers` 和 `scheduler_owner` 环境级边界。
- 阻断日志只记录操作名和替代入口，不记录 Prompt、邮件正文、Token 或消息内容。

## 验证

- P1.5 专项与受影响 V1/V2 回归：133 项通过（17.67 秒）。
- 项目 `tests/` 全量：567 项通过、1 项失败、1 条弃用 warning（26.78 秒）。
- 唯一失败仍是 `test_five_groups_overlap_with_limits_order_and_failure_isolation` 的固定 0.45 秒机器计时阈值；业务结果和并发上限断言均通过，本次耗时 0.474 秒。该问题按计划归入 P2.1，不在冻结轮次放宽阈值。
- Python compileall 通过。
- 前端 production build 通过：TypeScript 与 Vite 成功，4596 个模块完成转换，Vite 约 5.13 秒。
- `git diff --check` 通过，仅有工作区既有 LF/CRLF 提示。
- 全部测试使用本地 Fake/临时数据库；未调用真实 AI、SMTP 或微信。

## 部署与观察期

1. 提交后由 Alter 安全重启 8766，确认 `/api/system/status` 显示 `read_only`。
2. 至少观察 7 个完整调度日的 `groupbrief.legacy_v1` 阻断日志，识别是否仍有合法旧客户端。
3. V1 历史读取、SQLite 表和旧输出至少保留 30 天；没有调用证据后再独立退役旧写服务。
4. 最终删除路由、表或依赖必须另开轮次，不与本冻结提交混在一起。

本轮不会通过真实邮件/AI/微信做验收，也不会删除或改写历史数据。

## 回滚

- 紧急兼容时设置 `LEGACY_V1_WRITE_MODE=maintenance` 并安全重启 8766；不要重新启用旧 Windows 调度任务。
- 没有 Schema 或生产数据变更，代码可整体 revert。
- maintenance 只用于短期回滚窗口；它会恢复 V1/V2 双状态和重复外部调用风险。
