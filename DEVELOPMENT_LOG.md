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
- 已 push

---

## P2 — 消息标准化 + 精确排行榜（2026-08-17）

### 状态：P2 PASS

### 做了什么
- `MessageNormalizer`：RawMessage → NormalizedMessage，系统消息过滤（入群/退群/撤回/群名变更），10 种用户消息类型全部计入，连续消息不合并
- `RankingEngine`：确定性统计（总数/发言人数/Top10），同名并列按名称稳定排序，输出格式与文档示例一致
- 排行榜完全由程序计算，不经过任何 LLM

### 测试结果
- pytest 13 passed（系统过滤、类型计入、连续消息、数字正确性、格式、确定性）
- fixture 冒烟：group-a 一天 raw=685 → countable=668，Top10 格式与文档一致

### Commit
- `feat: add deterministic group ranking engine`（已 push）

---

## P3 — 多群 + 日期规则 + 生成服务（2026-08-17）

### 状态：P3 PASS

### 做了什么
- `ReportService.generate`：Run → GroupRun（每个群独立状态）→ 读取 → 标准化 → 排行 → Report 入库
- 防重复执行：同 report_date+group+range 已成功则跳过；force=true 允许重新生成
- 多群批量：group=None 生成全部启用群；周日返回 skipped；单群失败不影响其他群（partial）
- 日期规则测试：周一=周六+周日汇总；周二~周六=前一天；周日=不执行；邮件主题（含周末汇总版）
- `/api/reports/generate`（支持 report_date/group_id/force）、`/api/reports/{id}/prompt` 保存接口
- PromptService + DeepSeekV4FlashProvider 基础实现（无 API Key 时 prompt_status=skipped，不阻塞流程）

### 测试结果
- pytest 18 passed（新增日期规则 5 项）
- 多群冒烟：2 群成功（mock 737/821 条），dedupe 生效，force 生效，API 200

### Commit
- `feat: add multi-group and calendar rules`（待 push）

---
