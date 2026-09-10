# GroupBrief 总览实时任务节点与日志窗口任务

## 背景

当前“运行总览”能展示每天各群的结果卡片，但 00:15 后台任务运行期间缺少实时阶段和日志视图。现有权威状态已经保存在 `output/.scheduler/<run_date>.json` 与各群 `run.json`，日志已经按固定分类写入 `logs/`。

## 目标

- 在总览统计卡片下方新增“任务节点”和“运行日志”两个只读窗口。
- 任务节点使用最新 scheduler/run 状态，展示全局阶段和每群当前阶段。
- 日志通过固定白名单、日期过滤、条数限制和脱敏后返回结构化数据。
- 今日运行中自动刷新，历史日期保持手动刷新。

## 允许修改范围

- `app/scheduler/runtime_status.py`
- `app/api/v2_ui_read.py`
- 新增只读日志解析服务及对应后端测试
- `frontend/src/api.ts`
- `frontend/src/pages/v2/Dashboard.tsx`
- Dashboard 相关样式、Vitest 与 Playwright 测试
- V2 路由契约测试
- 本任务文件

## 禁止修改范围

- 不修改 00:15/08:30 调度时间或 APScheduler 注册逻辑。
- 不修改取数、排行、Prompt、生图、发送算法及状态推进逻辑。
- 不修改数据库 Schema、生产数据、发送锁、未知结果保护或外部 Provider 配置。
- 不触发真实取数、AI 调用、图片生成、微信发送、服务重启或部署。
- 不读取或写入 `.env`、凭据、Cookie、浏览器数据。

## 已确定实现要求

1. 抽取每日状态的纯读取构建函数；Dashboard 直接基于最新 scheduler 和启用群 run 快照生成 `runtime`，现有状态文件写入合同保持兼容。
2. 节点固定为：调度启动、读取群消息、生成排行榜、摘要与提示词、生成图片、等待发送/发送完成。
3. 节点状态固定为：`pending`、`running`、`success`、`retry_pending`、`held`、`failed`。
4. 图片 `image_job.status=queued` 只能显示排队/等待，不得冒充正在生成。
5. 损坏状态 fail-closed 为需要关注，不写回、不猜测成功。
6. 新增 `GET /api/v2/runtime/logs`；`tail` 默认 100、范围 1..200；来源只允许 `scheduler/app/provider/ai`。
7. 日志记录包含时间、级别、来源、消息；按运行日期过滤，跨来源按时间排序，消息最长 500 字符并脱敏。
8. 桌面端任务节点/日志按约 60%/40% 并排，820px 以下上下排列。
9. 今日 `not_started` 每 30 秒刷新，`running/retry_pending` 每 3 秒刷新，终态停止；页面后台暂停，恢复可见立即刷新；历史日期仅手动刷新。
10. 日志支持来源/级别筛选、暂停/继续自动滚动和手动刷新。

## 验收标准

- Dashboard 响应新增 `runtime`，旧字段与原 URL 保持兼容。
- 六群可展开查看当前节点；空状态、运行中、重试、暂停、失败、完成均有准确文案。
- 日志接口拒绝非法来源/级别/日期/条数，不能读取任意路径，不能泄露敏感字段。
- 现有 Dashboard 生成、发送与人工核对操作仍只命中原接口。
- 窄屏无横向溢出；轮询会按状态和页面可见性启停。
- 所有定向后端测试、前端测试、构建和 Dashboard E2E 通过。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_status.py tests/test_runtime_logs.py tests/test_v2_ui_router_contract.py tests/test_p24_observability.py -q
npm --prefix frontend test -- --run src/api.test.ts src/pages/v2/dashboardRuntime.test.ts
npm --prefix frontend run build
npm --prefix frontend run test:e2e -- --grep Dashboard
git diff --check
```

## 返回格式

- 汇报后端状态/日志接口、前端两个窗口、自动刷新与安全边界。
- 提供测试命令和准确通过/失败数量。
- 汇报 Git 状态、提交哈希、分支、仓库地址与推送结果。
- 明确生产服务未重启，真实外部任务未触发。
