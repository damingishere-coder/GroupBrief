# GroupBrief 无人值守稳定性整改任务

## 背景

依据 `GROUPBRIEF_RELIABILITY_AUDIT.md`，完成 P0-1 至 P2-3 的全部整改。目标是在外部 AI、图片和微信均可被 Mock 的情况下，证明 30 天 × 全部启用群的任务不会永久丢失、不会无限重试、不会重复生图或重复发送，并可从持久化 checkpoint 恢复。

## 允许修改范围

- `app/`：V2 调度、状态、Pipeline、Provider、图片、发送、日志和系统状态。
- `scripts/`：隔离仿真和 Windows 调度安装参数。
- `tests/`：单元、集成、并发、故障注入和 30 天仿真。
- `frontend/`：仅 P2-2 警告修复及相应测试。
- `requirements*.txt`：仅在已有依赖无法完成目标时修改；优先复用 Pillow、APScheduler、SQLModel。
- 本任务文档与审计报告中的整改状态说明。

## 禁止修改范围

- 不读取、写入或提交 `.env`、Token、API Key、密码、Cookie、浏览器数据。
- 不在验证中调用收费 AI、生图、真实微信或邮件。
- 不自动解除 `SEND_RESULT_UNKNOWN`，不把结果未知改成自动重发。
- 不改写历史 Git，不强推，不部署，不迁移生产数据库，不修改真实 Windows 任务或实时 `output/`。
- 不更换 FastAPI、APScheduler、SQLite/SQLModel，不做与稳定性无关的大规模重构。

## 已确定实现要求

| 编号 | 实现要求 | 权威验收证据 |
|---|---|---|
| P0-1 | invocation 完成与逐群终态分离；retryable/manual/final 正交执行状态；checkpoint、retry budget、`next_retry_at`、输入/产物 hash、attempt 记录 | 一次性故障后只续失败阶段；成功阶段调用计数不增加 |
| P0-2 | 最近 30 天 watchdog，旧到新补任务；明确未提交的历史发送可续；unknown 永久 hold；唯一 owner/busy backlog | 48 小时离线、跨日、重复启动测试零任务丢失/零重复发送 |
| P0-3 | 生图 L1 原 Prompt、L2 事实保持安全化 Prompt、L3 Pillow 本地信息图 | 审核拒绝与 Provider 不可用仍得到合同图片；topic/ranking 不变 |
| P0-4 | 微信数据读取按错误分类有限 retry、指数退避、熔断；不静默混合不兼容数据源 | timeout/5xx 可恢复，永久故障不形成重试风暴，快照来源可审计 |
| P1-1 | 所有发送 claim 更新必须检查；pre-submit 失败零外部调用，post-submit 失败进入 unknown | 每个更新点故障注入，发送调用和最终状态符合合同 |
| P1-2 | 复用、READY 和发送前统一强图片合同；可信已落盘图片可对账恢复 | 截断/错尺寸拒绝；落盘后崩溃不重复生图 |
| P1-3 | 发送逐群异常隔离；全局群同步失败只 hold 不可信群；桌面状态未知时熔断 | 首群明确 pre-submit 失败不影响后续，未知时不继续冒险 |
| P1-4 | 最终 Prompt 长度硬限、控制字符清理、事实优先裁剪、Provider 错误分类 | 中文/emoji/换行/超长属性测试稳定且不改变 topic id |
| P1-5 | JSON 结构日志；`runtime/YYYY-MM-DD/status.json`；run/group/stage/attempt/duration/model/code/error | 给定 date/task id 能恢复完整诊断；日报由逐群状态原子重建 |
| P1-6 | Scheduler owner lease、跨进程状态互斥/version、busy 持久补偿 | 双进程压力测试无 lost update、死锁、重复外部动作 |
| P2-1 | 并发测试改用 barrier/可控同步 | Windows 循环 100 次无偶发失败且仍能捕获串行退化 |
| P2-2 | 清理 Starlette/httpx 和前端 SSR warning，不扩大依赖风险 | 完整后端和前端测试无对应 warning |
| P2-3 | liveness/readiness/scheduler heartbeat/dependency/daily completion 分层 | 进程活着但依赖失败时 liveness=ok、readiness=degraded，原因明确 |

## 兼容性与安全不变量

1. 现有阶段状态和值保持可读，旧 `run.json` 可惰性补齐新字段。
2. `SENT` 永不被自动生成/发送路径降级或重发。
3. `CORRUPT`、`PROMPT_RESULT_UNKNOWN`、`SEND_RESULT_UNKNOWN` 永久 fail-closed。
4. 已启动且结果未知的付费调用不自动重试；L3 本地渲染不等同重复付费调用。
5. 所有状态写入原子化；同一 date × group 的读改写具备跨进程协调或版本检查。
6. 历史发送前重新验证当前目标和图片合同；不能证明“尚未提交”时不发送。
7. 日志和日报不得保存凭据或完整聊天/Prompt。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts\simulate_reliability.py --days 30 --seed 20260827 --groups 6
npm test --prefix frontend
npm run build --prefix frontend
git diff --check
```

## 返回格式

- 每个 P0/P1/P2 项：修改文件、行为变化、故障注入证据、剩余风险。
- 明确区分 Mock 验证与真实外部动作；不得用测试通过声称真实发送或真实 Provider 可用。
- 最终报告 Git 分支、提交、远端与推送结果。
