# GroupBrief V1 DEVELOPMENT_LOG

> 开发模型：DeepSeek V4 Flash
> 仓库：https://github.com/damingishere-coder/GroupBrief.git

---

## P0 — 项目骨架（2026-08-17）

### 状态：P0 PASS

### 做了什么
- 初始化 Git 仓库并绑定 GitHub remote（仓库原本为空）
- 建立 Python FastAPI 后端骨架 + SQLite（SQLModel）+ 配置（pydantic-settings）+ 分类日志
- 建立 React + Vite + TypeScript 前端骨架（Apple 蓝白风格，导航：仪表盘/群聊管理/执行记录/配置设置/关于）
- 建立统一消息模型、ChatHistoryProvider / PromptGeneratorProvider 抽象、V2 预留接口
- 建立日历规则引擎（周一～周日统计规则，测试通过）
- 建立群聊 CRUD、设置、执行记录、系统状态 API
- `start_windows.bat` 一键启动脚本
- 日期规则单测通过：周一=周六+周日汇总；周二~周六=前一天；周日=不运行

### 端口决策
- 文档默认 8765 被本机其他项目（New project 2）占用，经用户确认改用 **8766**。

### 测试结果
- 后端 `uvicorn app.main:app` 启动正常，/api/system/status、/api/runs、首页 200
- 前端 `npm run build` 成功

### Commit
- 待提交：`chore: initialize GroupBrief project`

### Push 状态
- 已 push（master 分支）

---

## P1 — 微信历史读取 Provider（2026-08-17）

### 状态：P1 PASS（真实微信环境：REAL_ENV_PENDING）

### 做了什么
- 实现 `ChatHistoryProvider` 统一接口 + `ProviderStatus` 状态机（OK/UNAVAILABLE/UNSUPPORTED_WECHAT_VERSION/GROUP_NOT_FOUND/READ_FAILED/EMPTY_RESULT/INVALID_RESULT）
- `WeChatDataAnalysisProvider`：探测微信数据目录（WeChat Files / xwechat_files），支持读取 WeChatDataAnalysis 导出的 JSON（data/wechat_export）；真实环境未配置时返回明确状态
- `WechatCliProvider`：契约式调用 wechat-cli（export/list-groups），命令不可用时返回 UNAVAILABLE
- `MockProvider` + fixtures 生成器（scripts/generate_fixtures.py）：生成两个群的 8 天中文模拟聊天（856+ 条/天，覆盖 text/image/emoji/link/quote/red_packet/voice/system/file/video）
- `HistoryService`：自动降级链 主→备→Mock，不静默失败
- `/api/system/providers`（状态）、`/api/groups/discover`（发现群）、`/api/groups/{id}/test-read`（测试读取）

### 测试结果
- pytest 7 passed：mock health、list_groups、范围读取（消息量/发言人/消息类型）、不存在的群、fallback、health 状态、provider 顺序

### 已知问题
- 本机未安装微信/WeChatDataAnalysis/wechat-cli，真实读取状态为 UNAVAILABLE，已自动降级 Mock 继续开发；最终联调需用户提供真实环境

### Commit
- `feat: add WeChat history providers`

### Push 状态
- 待 push

---
