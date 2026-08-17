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

## P9 — 稳定性 / 测试 / 文档 / 验收（2026-08-17）

### 状态：P9 PASS

### 做了什么
- 邮件发送增加 2 次重试（指数间隔）
- README.md（完整：功能/安装/启动/配置/Provider/DeepSeek/邮件/自动任务/文件输出/隐私/故障）
- LICENSE（MIT）
- V1_ACCEPTANCE_REPORT.md（20 项验收说明，结论：GroupBrief V1：PARTIAL，待真实外部配置）
- 清理测试残留数据（data/、output/），用户首次启动自动重建干净数据库
- 最终测试：pytest 32 passed + 前端 build 通过 + 端到端冒烟 PASS

### Commit
- `fix: improve Windows stability and error recovery`（737a0fc，含文档）

### Push 状态
- 已 push，本地 = GitHub 最新（master），工作区干净

---

## V1 结论

```
GroupBrief V1：PARTIAL
```

10 个阶段全部 PASS（代码+自动化测试）；真实微信读取 / DeepSeek 调用 / 邮件发送三项
需真实外部环境（微信数据、API Key、SMTP）联调，补齐后即转完整 PASS。

## P8 — Apple 风格本地 Web UI（2026-08-17）

### 状态：P8 PASS

### 做了什么
- 仪表盘：状态卡（统计日期/群数/下次执行/服务状态）+ Provider 状态区（WeChatDataAnalysis / wechat-cli / Mock 可用性）+ 群列表
- 群聊管理：多群 Tab + 添加群聊（支持从 Provider 发现的群列表一键选择）、删除/启停、生成/重新生成/全部生成、复制/导出排行榜与 Prompt、Prompt 编辑保存、手动发邮件、邮件预览
- 执行记录、文件管理（按日期查看 handoff 文件并下载）、日志（5 类日志 tail 查看）、配置设置（Key 掩码）、关于
- 海报预览 V2 占位（poster_file/poster_status 预留）
- 新增 /api/logs 日志接口

### 测试结果
- 前端 `npm run build` 通过（tsc 严格模式）
- 端到端冒烟：12 个 API + 首页全部 200，手动生成 run 成功

### Commit
- `feat: add GroupBrief local web interface`（待 push）

---

## P6 — 邮件（2026-08-17）

### 状态：P6 PASS（真实发信：REAL_ENV_PENDING）

### 做了什么
- `EmailService`：SMTP SSL/TLS、UTF-8（中文+emoji）、每天一封、每群=排行榜+Prompt、无额外内容
- 发送前检查：ranking_status=success 且数据存在；无数据不发送；SEND_PARTIAL_REPORT=true 时部分失败仍发送成功群
- 邮件主题：普通日 `群报 GroupBrief｜2026-08-14`、周一 `周末汇总` 版
- `/api/email/preview`、`/api/email/send`

### 测试结果
- pytest 30 passed（内容组装、无多余分析内容、主题、未配置不发信、部分成功策略）
- 预览冒烟：正文只含排行+Prompt

### Commit
- `feat: add email delivery`（已 push）

---

## P7 — Scheduler 自动任务（2026-08-17）

### 状态：P7 PASS

### 做了什么
- APScheduler BackgroundScheduler（Asia/Shanghai）：08:45 GenerateDailyReports、09:00 SendDailyEmail，misfire_grace_time=3600、coalesce=True 防堆积
- 周日由日历规则跳过（job 函数内二次判断）
- main.py lifespan 自动启停调度器（GROUPBRIEF_NO_SCHEDULER=1 可禁用，用于测试）

### 测试结果
- pytest 32 passed（两个 job 配置正确、自动任务函数可执行）
- 冒烟：服务启动后调度日志正常，/api/system/status 显示 next=08:45

### Commit
- `feat: add scheduled daily jobs`（待 push）

---

## P5 — 本地文件输出 + V2 Handoff（2026-08-17）

### 状态：P5 PASS

### 做了什么
- `HandoffService`：按 `output/YYYY-MM-DD/{群}/` 输出 5 个文件（ranking.txt / image_prompt.txt / meta.json / normalized_messages.json / handoff.json）
- Windows 非法字符安全目录名（/ \ : * ? " < > | 空格 → -）
- handoff.json 按文档 §18 结构（version=1、poster_file=null、status=prompt_ready，V2 预留）
- 文件管理 API：/api/files/dates、/{date}、/{date}/{group}/raw/{file}
- Report 表记录 ranking_file/prompt_file 路径

### 测试结果
- pytest 26 passed（安全目录名、5 文件生成、handoff 结构、meta 数据、双群隔离不串文件、日期规则联动）

### Commit
- `feat: add V2 handoff protocol`（待 push）

---

## P4 — DeepSeek V4 Flash Prompt Generator（2026-08-17）

### 状态：P4 PASS

### 做了什么
- `DeepSeekV4FlashProvider`：OpenAI 兼容 API 调用、按 CHUNK_MESSAGE_COUNT 分块分析事件 → 合并生成最终海报 Prompt、重试（指数退避）、超时、明确错误信息
- Prompt 规则内置：不编造事件/人物/金额/时间/地点；海报人物依据聊天事件人物而非排行榜；周一标题倾向"群里热闹这两天！"
- `TemplatePromptProvider`：未配置 API Key 时用本地模板生成（完全基于真实统计与消息摘录，不调用 LLM），保证邮件/UI/文件全链路可交付
- `PromptContext.build_context` 上移到基类

### 测试结果
- pytest 23 passed（模板结构、真实发言者、DeepSeek chunk 逻辑、无 Key 降级模板、PromptService 集成）

### 已知问题
- 真实 DeepSeek API 调用需用户提供 API Key（REAL_ENV_PENDING）

### Commit
- `feat: add DeepSeek image prompt generator`（待 push）

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
