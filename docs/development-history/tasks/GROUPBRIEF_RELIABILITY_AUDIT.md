# GroupBrief 无人值守稳定性审计报告

> 审计日期：2026-08-27（Asia/Shanghai）
> 代码基线：`f7f391824c03d8be45b20558cee6e5d74a2ce20c`
> 审计目标：判断当前系统在无人查看、无人重试、无人临时消歧的条件下，连续运行 30 天时能否稳定完成全部群的读取、统计、整理、Prompt、生图、保存和微信发送。
> 本轮边界：只审计并新增本报告；未修改生产代码、测试、配置、数据库、Windows 任务或运行状态；未调用收费 AI、未生图、未发送微信或邮件。

## 结论摘要

当前 GroupBrief 已经具备一批重要的安全保护：逐群 `run.json`、阶段状态、原子替换、损坏状态 fail-closed、Prompt/发送 claim、未知发送结果人工锁、群级生成隔离、图片候选归属和哈希校验、当天启动补偿等。这些措施使系统比“单个 `run_daily_task()` 黑盒”可靠很多，也显著降低了重复付费生图和重复发送的风险。

但当前版本**尚未达到 30 天无人值守标准**。决定性问题不是单次正常路径，而是失败后的自动收敛：

1. 任意正常返回的失败或部分失败批次都会写入 `generation_completed_at`；普通 `FAILED` 群又被启动恢复扫描排除，临时故障可能因此成为永久欠账。
2. 启动补偿和发送扫描都只看当天，程序跨日停机后不会自动补历史任务；当前机器也没有可证明有效的 Windows 开机启动链。
3. AI 生图只有原 Prompt 路径，没有安全化 Prompt 和本地确定性信息图降级；内容拒绝或模型故障仍会导致当天无图。
4. 外部发送与本地状态提交之间仍有未检查的 claim 更新窗口；系统虽然会对未知结果 fail-closed，但无法无人值守地完成消歧。
5. 批次状态与六个逐群状态缺少统一可信终态。审计时真实状态是“六群均 `SENT`，批次仍 `blocked`”。

隔离仿真使用固定种子 `20260827`，运行 30 天 × 6 群共 180 个任务单元，保留真实 `DailyPipeline`、`RunStore`、`DailyScheduleState` 和发送阶段，Mock 外部取数/AI/生图/微信。最终 130 个到达 `SENT`，21 个 `FAILED`，17 个图片就绪但从未发送，12 个因离线日根本未建档；没有发现无限 retry 或重复发送。**50/180 个任务未完成，说明当前实现偏向“安全停住”，还没有“自动恢复并最终完成”的闭环。**

---

## 1. 当前真实架构

### 1.1 运行形态与真实基线

审计在 2026-08-27 10:20（北京时间）记录到：

- `127.0.0.1:8766` 的真实监听链为：`python.exe -m uvicorn app.main:app`（PID 211548）← `.venv\Scripts\python.exe -m uvicorn`（PID 69232）← `alter.exe --internal-daemon`（PID 48056）。
- Alter 中 `GroupBrief-Backend` 为 running、enabled、autorestart，当前 restart count 为 2；这能覆盖 Alter 仍存活时的子进程崩溃。
- `/api/system/health` 返回 200/`ok`；`/api/system/ready` 返回 ready；`/api/system/status` 显示 scheduler owner=`fastapi`、scheduler active=`true`、6 个启用群、下一次生成时间为次日 00:15。
- WeChatDataAnalysis 的本地健康端点 `127.0.0.1:10392/api/health` 当时为 healthy。
- Windows 任务 `GroupBriefDaily` 与 `GroupBriefDailySend` 均为 Disabled、Interactive only、`StartWhenAvailable=false`、无失败重启策略；未发现 GroupBrief/Alter 对应 Windows Service、Run 启动项或 Startup 快捷方式。
- 因此，**已证实的是 Alter 对已启动子进程的守护，不是 Windows 重启后的自动拉起**。

实时证据只表示审计时刻的状态，不等价于未来 30 天持续健康，也不证明外部 AI 或微信发送成功。

### 1.2 主要组件

| 组件 | 责任 | 主要代码位置 | 持久化 |
|---|---|---|---|
| FastAPI 生命周期 | 初始化配置、目录、数据库、启动检查和内置 Scheduler | `app/main.py:50-89` | 数据库、日志 |
| APScheduler owner | 00:15 生成、每分钟扫描发送、当天启动补偿 | `app/scheduler/manager.py:54-149` | `.scheduler/<date>.json` |
| 每日任务包装器 | 生成锁、批次状态、结果汇总、邮件决策 | `app/scheduler/daily_v2_job.py:163-330` | `.scheduler/<date>.json` |
| DailyPipeline | 多群并发、逐群阶段编排、图片任务、发送扫描 | `app/pipeline/daily_pipeline.py:68-453` | 逐群输出目录 |
| 微信数据源 | MCP/导出读取、分页、去重、总时限 | `app/data_sources/wechat_data_analysis.py:41-138`、`app/providers/history/wechat_data_analysis.py:257-579` | `messages.json` |
| 统计与 Prompt | 排行、候选话题、结构化 Prompt、事实校验 | `app/pipeline/generation_stages.py:229-716`、`app/ai/prompt_builder.py:207-587` | ranking、prompt、run.json |
| 生图执行器 | Codex 进程、job/claim、候选归属、验证、原子提升 | `app/image/codex_generator.py:272-1455`、`app/image/image_task.py:69-266` | 图片、attempt manifest、run.json |
| 微信发送阶段 | 目标预检、文字/图片 claim、未知结果锁 | `app/pipeline/delivery_stages.py:38-483` | run.json |
| RunStore | 状态机、原子 JSON 写入、Prompt/发送 claim | `app/v2/run_store.py:117-812` | `output/<group>/<date>/run.json` |
| 恢复服务 | 启动扫描未完成任务、未知发送 fail-closed | `app/v2/recovery.py:49-199` | 读取/推进 run.json |

### 1.3 当前状态权威来源

- 逐群事实权威：`output/<群>/<YYYY-MM-DD>/run.json`。
- 批次/邮件事实：`output/.scheduler/<YYYY-MM-DD>.json`。
- 当前缺少一个把二者一致性校验后汇总的“每日可信终态”。
- 2026-08-27 的真实样本中，批次文件为 `generation_status=blocked`、`email_status=skipped_generation_not_successful`，但当前六个群的 `run.json` 都是 `SENT`。这证明两层状态可能长期分叉。

---

## 2. 每日任务完整调用链

### 2.1 当前生产 DAG

```text
FastAPI lifespan
  └─ start_scheduler()
      ├─ 00:15 run_daily_v2_job(date)
      │   ├─ generation_mutex (thread + Windows named mutex)
      │   ├─ DailyScheduleState.load(date)
      │   ├─ 当 generation_completed_at 不存在时：
      │   │   └─ DailyPipeline.generate_all(date)
      │   │       ├─ PeriodResolver -> [前一自然日 00:00, 当日 00:00)
      │   │       ├─ GroupNameSyncService
      │   │       ├─ repo.list_groups(only_enabled=True)
      │   │       └─ 每群 worker（群间隔离）
      │   │           ├─ 载入/初始化 run.json
      │   │           ├─ WeChatDataAnalysisSource.fetch_messages()
      │   │           ├─ 保存 messages.json
      │   │           ├─ RankingEngine + RankingRenderer
      │   │           ├─ 保存 ranking.json / ranking.txt
      │   │           ├─ GroupSummaryImagePromptBuilder.build()
      │   │           ├─ 保存 image_prompt.txt + prompt_meta
      │   │           └─ ImageStages
      │   │               └─ CodexImageGenerator.generate()
      │   │                   ├─ 进程/线程 job claim
      │   │                   ├─ imagegen API/CLI
      │   │                   ├─ 候选归属、路径、哈希、PIL/尺寸检查
      │   │                   └─ os.replace -> daily_image.png
      │   ├─ 写 generation_completed_at（目前成功/部分/失败都写）
      │   └─ 成功时进入邮件；失败/blocked/partial 可跳过邮件
      └─ 每分钟第 15 秒 DailyPipeline.send_due(now)
          ├─ 只读取 now.date() 的 run.json
          ├─ 到点且不超过 late window
          └─ DeliveryStages
              ├─ verify target / image preflight
              ├─ text claim -> send_text -> finish claim
              ├─ image claim -> send_image -> finish claim
              └─ SENT，或 SEND_RESULT_UNKNOWN/manual hold
```

### 2.2 节点级失败点地图

缩写：`有/无/部分`；“恢复”指进程崩溃后无需人工即可继续；“永久丢失”指在当前自动调度策略下可能永远不再推进。

| 关键步骤 | 输入 | 输出 | 外部依赖 | 异常捕获 | timeout | retry | fallback | 状态持久化 | 崩溃后恢复 | 重复执行副作用 | 当天永久丢失 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Scheduler 触发 | 当前时间、配置 | 每日 job | APScheduler、常驻进程 | 有，顶层记 failed | misfire 30 分钟 | 无队列 | 仅当天启动补偿 | 批次 JSON | 部分 | 锁冲突会直接放弃 | **高**，跨日不补 |
| 日期范围 | run_date、时区 | 前一自然日窗口 | zoneinfo | 有效日期校验 | 不适用 | 无 | 无 | 写入 run.json | 有 | 幂等 | 低；但业务若需非自然日会错数 |
| 群识别/群名同步 | DB 群、WDA 群列表 | 绑定与发送目标 | SQLite、WDA | 批次前置异常未隔离 | WDA 子调用有 | 无 | 手工 target | run.json 审计字段/DB | 部分 | 全局失败可阻断所有群 | **高** |
| 聊天读取 | group id、时间窗 | V2Message[] | WDA MCP/导出 | 有，单群 FAILED | 单次 + 总时限 | 无请求级 retry | 仅配置选择；MCP 运行时不切 CLI | `messages.json`、run.json | 快照已保存则可复用 | 快照避免重抓 | **高**，FAILED 自动排除 |
| 内容过滤/快照解析 | 消息列表 | 合法消息快照 | 本地文件 | 有，损坏 fail-closed | 不适用 | 无 | 不隐式重抓 | 原子 messages.json | 有条件 | 重复复用无副作用 | 中；损坏需人工 |
| 排行榜统计 | messages | ranking result/text | 本地 CPU | 有，单群 FAILED | 无 | 无 | 无 | ranking.json/txt、run.json | 文件已落盘可复用 | 基本幂等 | 高，FAILED 被封存 |
| AI 内容整理/选题 | messages、排行 | topic selection | Codex/DeepSeek | 解析错误有重试；调用异常部分不在重试块 | 有 | 部分 | Codex/DeepSeek 配置 fallback，未知结果 fail-closed | operation claim、prompt_meta | 部分；未知结果人工 | 盲重试可能重复付费，因此被锁 | **高** |
| Prompt 渲染 | 结构化选题、昵称、引用 | image_prompt.txt | 本地 + 上游 AI | Schema/事实检查有 | 上游有 | 格式重试部分有 | 无安全化 Prompt | 原子 prompt 文件、hash、run.json | 已保存可续 | 输入 hash 降低重复 | 高，超长/敏感内容无降级 |
| 生图调用 | prompt file | 候选图片/回执 | Codex imagegen | 分类捕获 | 默认 1200 秒 | 仅确认 start_failed 最多 2 次 | 无 L2/L3 | job/attempt manifest、claim | 部分；未知回执 hold | 避免重复付费 | **高** |
| 图片返回/归属 | job/thread candidate | 可信候选 | Codex 线程输出 | 有 | 属于总调用时限 | 无独立下载 retry | 同线程候选恢复 | manifest/hash | 部分 | 强归属避免串群 | 高，失败即无图 |
| 图片有效性检查 | 图片文件 | valid/invalid | Pillow | 新图强、复用/发送前弱 | 不适用 | 无 | 无 | 校验结果部分记录 | 部分 | 截断 PNG 可能被复用 | 中高 |
| 图片保存/归档 | 临时图片 | daily_image.png | NTFS、磁盘 | 原子替换前后捕获不完整 | 不适用 | 无 | 保留部分候选 | 文件 + run.json | 存在“文件成功/状态失败”窗口 | 可触发重复生图或 hold | 高 |
| 微信文字发送 | 排行文本、target | SendResult | Windows 微信 UI | sender 内有；claim 更新结果有未检查路径 | 阶段/提交/互斥有 | 明确未提交可重试；未知不重试 | 无 | claim、sent/unknown 字段 | 未知提交需人工 | fail-closed 降低重复 | 中；会人工 hold |
| 微信图片发送 | daily_image.png、target | SendResult | Windows 微信 UI | 同上 | 有 | 明确失败可续图片且不重发文字 | 无 | claim、SENT/unknown | 部分 | 状态提交失败可能重复 | 中高 |
| 当天完成记录 | 群 run + 批次结果 | completed/SENT | 文件系统 | 有 | 不适用 | 无 | 无一致性汇总 | 两套 JSON | **不可靠** | 分叉导致错误判定 | **高** |

### 2.3 目标 DAG（最小增量，不换框架）

```text
Watchdog 扫描最近 30 天（旧 -> 新）
  └─ 为每个 date × enabled_group 建立/核对 task identity
      ├─ 读取 last_successful_checkpoint + input_hash
      ├─ 若 execution_state=WAIT_RETRY 且 now>=next_retry_at：续跑当前阶段
      ├─ 若 HOLD_MANUAL / SEND_RESULT_UNKNOWN：保持 fail-closed 并进日报
      └─ 按 checkpoint 推进
          DATA -> RANKING -> SUMMARY -> PROMPT
          -> IMAGE L1
               ├─ 可明确分类的安全拒绝：IMAGE L2 safe prompt
               └─ L2 仍失败：IMAGE L3 local infographic
          -> IMAGE_SAVED -> SEND_TEXT -> SEND_IMAGE -> SENT
              每一步：原子业务产物 + attempt ledger + checkpoint

Daily reconciler
  └─ 汇总逐群 run.json，校验批次状态，原子写 runtime/YYYY-MM-DD/status.json
```

---

## 3. 当前已有的稳定性措施

以下能力已经存在，后续整改不应重复造轮子：

1. **逐群状态机和输出隔离**：`PENDING → DATA_READY → RANKING_READY → PROMPT_READY → IMAGE_READY → READY_TO_SEND → SENT/FAILED`，每群每天独立目录。
2. **原子状态写入**：`RunStore`、`DailyScheduleState` 和图片提升使用临时文件 + `os.replace`；JSON 损坏时返回 `CORRUPT` 并阻止自动覆盖。
3. **消息快照复用**：`messages.json` 存在时默认不重新读取微信，避免重启导致数据漂移；快照损坏不会静默回源。
4. **多群生成隔离**：群 worker 和图片 worker 捕获单群异常，单个群的多数阶段故障不会直接终止其他群。
5. **Prompt 幂等保护**：记录 operation id/input hash；模型调用结果未知时暂停，不盲目重复计费调用。
6. **图片任务归属保护**：job id、thread id、prompt hash、候选路径约束、SHA、PIL 解码和目标尺寸验证；进程超时会尝试终止进程树并核对可信候选。
7. **生图有限重试**：仅在确认进程没有成功启动的 `start_failed` 场景最多尝试 2 次；已启动但结果未知不会自动重试。这是正确的成本/幂等边界，不应改成无脑 retry。
8. **发送 claim 与未知结果锁**：文字和图片分阶段记录；文字已确认而图片失败时可只续图片；`SEND_RESULT_UNKNOWN` 永久 fail-closed，不自动重发。
9. **目标与图片预检**：发送前核对群目标、候选归属、文件存在、hash/尺寸等部分信息，降低串群和错图风险。
10. **当天启动补偿**：FastAPI Scheduler 启动时会检查当天批次，在未写完成标记时补跑生成。
11. **并发约束**：生成有线程锁和 Windows 命名互斥；发送桌面操作也有互斥，减少多进程同时控制微信。
12. **测试覆盖基础较好**：本次完整后端测试 597 项通过，包含未知结果、claim、恢复、并发和发送幂等测试；前端 21 项通过。

这些保护解释了为什么仿真没有出现重复发送和无限重试；但它们也让临时失败更容易进入永久停止，因此必须补上“有预算的自动恢复”和跨日 watchdog。

---

## 4. P0 风险

### P0-1：失败/部分失败批次被永久标记为“生成已完成”

- **问题**：只要 `DailyPipeline.generate_all()` 正常返回，即使结果是 `partial`、`failed` 或 `blocked`，调度层仍写 `generation_completed_at`。启动补偿看到该字段后不再生成；恢复扫描又明确排除普通 `FAILED`。
- **为什么发生**：批次“本次调用已结束”和“所有群已完成”共用了同一个 completion marker；阶段状态和执行重试状态没有正交拆分。
- **代码位置**：`app/scheduler/daily_v2_job.py:231-293`、`app/scheduler/manager.py:115-148`、`app/v2/recovery.py:70-100`。
- **如何复现**：让任意群第一次取数 timeout 或 Prompt 返回错误；每日 job 正常返回 partial；检查 `.scheduler/<date>.json` 已有 `generation_completed_at`，该群 `run.json=FAILED`；重启服务，自动补偿不会再次处理。
- **修改方案**：保留“invocation completed”，新增 `batch_terminal_at` 或按逐群状态计算真正完成；`FAILED_RETRYABLE` 必须保留 retry budget、`next_retry_at` 和 last checkpoint；watchdog 根据逐群事实而不是批次 marker 补跑。
- **修改风险**：错误分类会造成重复付费调用。Prompt/生图“结果未知”必须继续 hold，只对明确未提交或确定失败自动重试。
- **验证成功**：注入一次性 timeout 后，第一次批次为 partial，重启在 `next_retry_at` 后只重跑失败群，成功群不重读、不重算、不重生图，最终统一状态为完成。

### P0-2：只补当天任务，跨日停机后任务永久丢失

- **问题**：启动恢复只检查当天；发送扫描也只构造 `now.date()`。错过 00:15 或发送时间并跨日后，历史欠账不再进入自动路径。
- **为什么发生**：Scheduler 设计假设进程一直在线；APScheduler misfire 只保留 30 分钟，发送 late window 也只有 30 分钟。
- **代码位置**：`app/scheduler/manager.py:54-149`、`app/pipeline/daily_pipeline.py:404-453`、`app/pipeline/daily_pipeline.py:941-1020`。
- **运行证据**：两项 Windows 任务均 Disabled、Interactive only、无 `StartWhenAvailable`/`RestartOnFailure`；未发现可证明的 Windows 开机拉起机制。Alter 只能证明当前守护关系。
- **如何复现**：在某日 00:15 前停止服务，次日再启动；观察只检查次日 run_date，昨日没有 `.scheduler`/`run.json`。或图片已准备后跨过发送日，后续 `send_due` 永远不扫描该日期。
- **修改方案**：启动和周期 watchdog 扫最近 30 天，旧到新创建/核对逐群任务；生成和“明确尚未提交”的发送可补跑；未知提交继续人工消歧。Windows 启动机制需单独建立并可验证。
- **修改风险**：历史群配置或群名可能已变化；任务 identity 应使用稳定 group id + report date，发送目标使用当天已持久化快照并重新做 action-time preflight。
- **验证成功**：模拟关机 48 小时后启动，昨日及前日缺失生成按旧到新补齐；已经 `SENT` 不重发；`SEND_RESULT_UNKNOWN` 不自动重发；日报明确列出人工 hold。

### P0-3：AI 生图没有 Level 2/Level 3 降级

- **问题**：原 Prompt 被内容审核拒绝、昵称/引用敏感、模型不可用或候选无效时，系统只记录失败；没有安全化 Prompt，也没有本地简化信息图。
- **为什么发生**：`CodexImageGenerator` 始终发送同一 Prompt，`ImageStages` 只有成功/失败状态；现有 regeneration 是人工审核流程，不是自动降级。
- **代码位置**：`app/image/codex_generator.py:898-909`、`app/image/image_task.py:169-200`、`app/pipeline/image_stages.py:63-137`、`app/image/regeneration.py:59-401`。
- **如何复现**：在话题引用中放入会触发审核的原话，或让 imagegen 返回审核拒绝；检查 run 进入 `IMAGE_GENERATION_FAILED`，无第二 Prompt、无本地图。
- **修改方案**：Level 1 保持现有 Prompt；仅对可明确分类的安全/内容拒绝进入 Level 2，从同一结构化 topic selection 生成安全化 Prompt，泛化昵称、原话和风险表达但保留 topic id、事实与排行；仍失败则 Level 3 用 Pillow 按结构化数据确定性渲染信息图，不再调用外部模型。
- **修改风险**：过度清洗会改变当天主题；Level 2 必须保存原/安全 Prompt、变更原因和结构化事实 hash，验证核心 topic id 集合不变。
- **验证成功**：构造审核拒绝，L1 失败后 L2 自动成功；再让 L2 失败，L3 仍产出 1024×1536 可发送图片；三层 attempt ledger 完整且主题/排行一致。

### P0-4：V2 微信数据源运行时故障可让所有群同日失败，且无自动后备

- **问题**：V2 固定构造 `WeChatDataAnalysisSource`。MCP 已配置但运行时 timeout/服务崩溃时不会切换到另一个读取后端，也没有请求级 retry；共用依赖故障可同时击穿全部群。
- **为什么发生**：当前 fallback 主要在“配置时选择”和 MCP 不支持范围方法时的同后端分页兼容，不是运行时 provider fallback。
- **代码位置**：`app/pipeline/daily_pipeline.py:80-88`、`app/data_sources/wechat_data_analysis.py:41-138`、`app/providers/history/wechat_data_analysis.py:257-369`。
- **如何复现**：保持 MCP client 配置有效但让 10392 在 fetch 时断开；六群分别得到读取失败，批次随后仍可能写 completed marker。
- **修改方案**：先加入同 provider 的有限指数退避；只有能证明两个后端返回相同语义/日期边界且配置允许时，才切换备用读取器。每次 fallback 记录 provider、错误分类、数据窗口和去重指标。
- **修改风险**：不同后端消息 ID、时区或分页语义不一致会造成重复/漏数；fallback 不能静默拼接两份不兼容快照。
- **验证成功**：主读取器前两次 timeout 后第三次成功；或受控切换备用后，消息窗口、去重数、群 identity 均通过合同测试，其他群不受影响，批次不被提前封存。

---

## 5. P1 风险

### P1-1：图片发送 claim 的关键更新结果未检查

- **问题**：文字发送开始会检查 `update_send_claim` 返回值，但部分完成更新、图片开始和图片完成路径忽略布尔结果，仍继续外部发送或返回 sent。
- **为什么发生**：状态写入被当作附属记录，而不是外部动作的事务前置/后置条件。
- **代码位置**：`app/pipeline/delivery_stages.py:215-329`、`app/pipeline/delivery_stages.py:381-442`、`app/v2/run_store.py:619-654`。
- **如何复现**：注入 stale claim 或让 `update_send_claim` 返回 false；观察图片仍可能发送，或外部成功但本地未形成可靠 SENT。
- **修改方案**：所有 pre-submit claim 更新失败必须禁止发送；post-submit 更新失败必须进入 `SEND_RESULT_UNKNOWN`，保存不可变 attempt id，不得返回普通 sent。
- **修改风险**：错误地把“状态写失败但外部未提交”归为 unknown 会增加人工 hold，但比重复发送安全。
- **验证成功**：对每个 claim 更新点注入 false/OSError；提交前零外部调用，提交后失败一律 unknown；重启不重复发送。

### P1-2：批次状态与逐群状态没有一致性收敛

- **问题**：真实环境出现六群全 `SENT`、批次仍 `blocked`；健康接口和邮件决策无法给出唯一可信答案。
- **为什么发生**：批次文件只记录某次生成调用，后续人工/恢复/发送不会统一重算批次终态。
- **代码位置**：`app/scheduler/daily_v2_job.py:215-330`、`app/scheduler/daily_v2_job.py:521-617`、`app/api/system.py:22-98`。
- **如何复现**：先产生 partial/blocked，再通过后续恢复或手工路径使群任务全部 SENT；批次状态保持旧值。
- **修改方案**：新增只读 reconciler，以逐群 run 为事实源计算 `complete/partial/held/missing`，原子写日报；不要反向覆盖逐群历史。
- **修改风险**：群配置当天变化会改变期望集合，需保存 daily task manifest。
- **验证成功**：任一路径推进后，reconciler 给出一致终态；篡改/缺失一个 run 时日报明确 degraded，不伪报成功。

### P1-3：发送扫描缺少最外层逐群异常隔离

- **问题**：`send_due` 串行循环没有包住整个单群 load/路径/持久化/DeliveryStages 调用；sender 自身异常虽被处理，但更外层异常可阻断后续群。
- **为什么发生**：生成路径有 worker 级隔离，发送路径没有对称设计。
- **代码位置**：`app/pipeline/daily_pipeline.py:404-453`、`app/pipeline/delivery_stages.py:56-483`。
- **如何复现**：让第一个 due 群的 run.json 读取或状态更新抛异常；验证后续群本轮没有发送。
- **修改方案**：每群完整 send transaction 外包 `try/except`，记录该群 retryable failure 后继续；同时保留桌面互斥避免并发控制微信。
- **修改风险**：异常后微信 UI 可能处于未知界面；仅当 sender 明确未提交且可恢复 UI 时继续下一个群，否则整个桌面发送器应短暂熔断并留下 backlog。
- **验证成功**：第一群 pre-submit 失败不影响第二至六群；post-submit unknown 时不重复且日报准确。

### P1-4：多群隔离在全局前置步骤不完整

- **问题**：`_sync_group_names()` 与 `_load_groups()` 位于群 worker 之前；前置异常会让所有群都没有逐群结果。
- **为什么发生**：批次级准备与逐群处理边界没有明确区分可降级/不可降级错误。
- **代码位置**：`app/pipeline/daily_pipeline.py:115-124`、`app/pipeline/daily_pipeline.py:184-219`。
- **如何复现**：让群名同步 list_groups 抛 timeout，或 SQLite 在 list_groups 时 locked；观察全部 worker 未启动。
- **修改方案**：群清单使用上一次成功快照；群名同步失败时标记 stale 并继续已绑定群，只有 identity 不可信的群单独 hold。
- **修改风险**：使用陈旧群名可能发错群；必须在发送前重新核对 target，且不可信时 fail-closed。
- **验证成功**：同步失败时已稳定绑定的群仍生成；未知/冲突群单独 hold；不出现串群发送。

### P1-5：Prompt 总长度、特殊字符和 Provider 重试边界不完整

- **问题**：输入分段约限制 50,000 字符，但最终图片 Prompt 没有总长上限；群名、昵称、引用可原样进入固定标题合同。解析重试覆盖 JSON/Schema 错误，但部分 `_analysis_chat()` 调用位于重试 `try` 外，网络/timeout 不进入格式重试。
- **为什么发生**：只限制了上游对话段和单字段，没有对最终产物做字节/token、控制字符和内容风险预算。
- **代码位置**：`app/ai/conversation_segments.py:46-52,108-140`、`app/ai/prompt_builder.py:337-388,448-583`、`app/ai/poster_copy.py:76-84,419-636`。
- **如何复现**：使用超长群名/昵称、含换行的昵称、成百条长引用；让 Provider 第一次 read timeout 或返回 Markdown fence/错误 JSON。
- **修改方案**：最终 Prompt 加 token/字符硬上限和确定性裁剪顺序；显示文本转义控制字符；结构化数据与渲染文本分离；Provider 错误按“明确未提交/429/5xx/timeout unknown/Schema”分类。
- **修改风险**：裁剪可能丢核心话题；优先保留 topic id、排名、事实摘要，引用和装饰性指令最后裁剪。
- **验证成功**：属性测试覆盖中文、emoji、换行、引号和最大长度；输出始终满足合同，核心 topic 不变，无无限重试。

### P1-6：图片复用/发送前校验弱于新图落盘校验

- **问题**：新图最终路径会用 Pillow 解码并检查 1024×1536；但已有图片跳过、部分候选复用和发送前主要只检查存在、非空和文件头。带合法 PNG 头的截断文件可能通过基础检查。
- **为什么发生**：`verify_image()` 与 `verify_image_contract()` 两套合同应用范围不同。
- **代码位置**：`app/image/image_task.py:69-102,133-200`、`app/image/codex_generator.py:438-449,1382-1420`、`app/pipeline/delivery_stages.py:143-210`。
- **如何复现**：把 daily_image.png 替换为仅含合法 PNG signature 的截断文件，保持非零大小；走复用或发送 preflight。
- **修改方案**：所有进入 `IMAGE_READY`、复用和发送前统一执行 Pillow `verify/load`、尺寸、格式和可选 SHA/mtime 合同；失败退回 image checkpoint，不发送。
- **修改风险**：Pillow 解码增加少量耗时；可缓存 hash 对应的验证结果。
- **验证成功**：截断、错尺寸、扩展名伪装、零字节全部被拒绝；合法图只验证一次并可安全复用。

### P1-7：图片原子提升后仍可能形成“文件成功、状态失败”

- **问题**：`os.replace` 已把图片放到目标路径后，smoke metadata、清理、回执删除或 hook 状态更新仍可能抛错/被吞掉，最终 run 标记 FAILED 但文件实际存在。
- **为什么发生**：文件 commit 与状态 commit 不是一个可恢复的 checkpoint；部分 hook 使用 `except Exception: pass`。
- **代码位置**：`app/image/codex_generator.py:1247-1295,1382-1420`、`app/image/image_task.py:232-266`、`app/pipeline/image_stages.py:63-144`。
- **如何复现**：在 `os.replace` 后、`_save_last_smoke` 或 run hook 中注入 OSError；重启后比较文件与 run 状态。
- **修改方案**：把“目标文件已通过强校验”的事实作为可重建 checkpoint；启动 reconciler 若 hash/尺寸/operation id 匹配则修复为 IMAGE_READY，不重新生图。
- **修改风险**：错误认领旧图会串日/串群；必须同时校验 group task id、run date、prompt hash、job id 和 SHA。
- **验证成功**：七个落盘窗口逐点 kill；重启均认领同一可信图片，imagegen 调用次数仍为 1。

### P1-8：锁冲突和普通状态更新缺少跨进程完整协调

- **问题**：生成锁 2 秒未取得就返回 `already_running`，没有持久队列；普通 `DailyScheduleState.update` 只有进程内锁，`RunStore` 的部分普通读改写也可能在多进程竞争时丢更新，命名互斥主要覆盖特定 claim 路径。
- **为什么发生**：系统同时支持 FastAPI owner、脚本/Windows task 和人工 API，多 owner 风险没有用唯一调度租约统一。
- **代码位置**：`app/services/generation_runtime.py:22-48`、`app/scheduler/daily_v2_job.py:163-183`、`app/scheduler/daily_v2_job.py:143-156`、`app/v2/run_store.py:225-253,561-654`。
- **如何复现**：两个进程同时启动相同日期 job，并在状态 read 与 replace 之间注入延迟；观察一个直接放弃或字段被覆盖。
- **修改方案**：确立单 Scheduler owner；busy 写入 backlog/`next_retry_at`；所有逐任务读改写使用同一跨进程 mutex 或带 version 的 compare-and-swap。
- **修改风险**：锁顺序错误会死锁；固定 batch→group→send claim 顺序并限制等待时间。
- **验证成功**：多进程压力测试中无 lost update、无死锁；busy 任务最终自动领取且只执行一次外部阶段。

### P1-9：缺少无人值守所需的结构化诊断和每日汇总

- **问题**：日志多为普通文本，缺少稳定 `run_id/group_task_id`、stage duration、attempt、模型、HTTP code、错误分类；`/health` 恒定 OK，`/ready` 只检查本地基础项；没有 `runtime/YYYY-MM-DD/status.json`。
- **为什么发生**：可观测性按“终端看报错”建设，而非按 30 天任务账本建设。
- **代码位置**：`app/core/logging.py`、`app/api/system.py:22-98`、`app/scheduler/daily_v2_job.py:163-330`。
- **如何复现**：查询某群某日第二次生图 timeout 的响应码、退避和耗时；当前无法从单一结构化来源回答。
- **修改方案**：结构化 JSON 日志 + 每日 reconciler；健康分为 process health、scheduler liveness、dependency readiness、daily completion，避免 200/OK 误导。
- **修改风险**：日志可能泄露聊天和 token；只记录 hash、长度、错误摘要，统一脱敏，不写原始 Prompt/聊天/API key。
- **验证成功**：给定 date+group_task_id 可还原所有 attempts；每日状态能回答每群卡在哪一步、下一次重试时间和是否需人工。

---

## 6. AI 生图专项风险

### 6.1 Prompt 风险清单

| 检查项 | 当前结论 | 风险 |
|---|---|---|
| Prompt 超长 | 对话分段有限制，最终 Prompt 无总上限 | Provider 拒绝、截断或超时 |
| Markdown/JSON 错误 | 有 fence/JSON/Schema/事实校验及有限格式重试 | 调用异常不一定进入重试；最终失败无降级 |
| 固定自然语言格式 | 渲染合同依赖固定标题/字段 | 昵称换行、控制字符、模型偏离可破坏结构 |
| 特殊字符/中文昵称 | 中文路径基本受 Path/UTF-8 支持；显示字段未统一转义 | 控制字符和引用边界污染 Prompt |
| 聊天安全限制 | 原话、昵称、敏感表达可能进入图片 Prompt | 单一话题可能使整图审核失败 |
| 单话题异常隔离 | 无按 topic 的安全风险隔离/替换 | 一个 topic 可拖垮整张图 |
| 事实保持 | 已有 topic selection、message id、quote 校验 | Level 2 设计必须复用这些事实锚点 |

### 6.2 当前生图错误分类与重试

| 场景 | 当前分类 | 自动 retry | backoff | 当前结果 |
|---|---|---:|---:|---|
| 可执行文件不存在/确认未启动 | `start_failed` | 最多 2 次 | 无 | 重试后失败 |
| 已启动后非零退出 | `result_unknown` | 否 | 无 | hold，防重复付费 |
| 进程 timeout | 尝试杀进程树和候选恢复；否则 unknown | 否 | 无 | 可恢复可信候选，否则 hold |
| 无可信回执/无效候选 | `result_unknown` | 否 | 无 | hold |
| 内容审核拒绝 | 未形成专门稳定分类 | 否 | 无 | 通常归失败/unknown |
| 429 | Codex CLI 路径未暴露稳定 HTTP 分类 | 否 | 无 | 无专项策略 |
| 5xx/网络 | 同上 | 否 | 无 | 无专项策略 |
| 图片下载失败 | 无独立 downloader retry 接口 | 否 | 无 | 生成失败 |

“所有异常一律 retry”不适合此链路：只要请求可能已经被 Provider 接收，就不能盲目再次付费生成。正确方向是扩展可证明的错误分类和 attempt ledger，而不是放宽 unknown 锁。

### 6.3 三级降级设计

**Level 1：原 Prompt**

- 完整保留现有 Prompt 合同、topic selection 和图片质量要求。
- 只对明确 `start_failed`、明确 429/5xx 且 Provider 证明未接受任务的场景做有上限的指数退避。

**Level 2：安全化 Prompt**

- 触发条件仅限明确内容安全/审核拒绝或本地安全预检命中。
- 输入必须是同一份已持久化结构化 topic selection、排行和事实 hash，不重新让 AI 自由选题。
- 只允许：昵称泛化为“群友 A/B”、原话改为事实性转述、敏感细节抽象、删除装饰性风险词。
- 禁止：删除主要 topic id、改变排行、编造事实、把失败主题替换成另一天内容。
- 保存 `prompt_level=2`、原/安全 Prompt hash、变更字段和触发错误分类。

**Level 3：本地简化信息图**

- 使用 Pillow 等本地确定性渲染器，读取 ranking.json、topic selection、日期和安全化摘要。
- 固定 1024×1536 布局，包含日期、活跃榜、主要话题、数据量和“简化版”标识。
- 不调用外部 AI，不受模型审核或配额影响。
- 使用同一图片合同和原子写入；`image_source=local_fallback` 明确记录，不伪装成 AI 漫画。

### 6.4 重试预算建议

| 错误类型 | 建议 |
|---|---|
| DNS/connect timeout、明确未提交 | 3 次；5s、20s、60s + jitter |
| 429 | 尊重 `Retry-After`；总预算不超过当日截止时间 |
| 5xx | 2-3 次指数退避；熔断共享 Provider，避免六群同时冲击 |
| read timeout/无回执/已启动未知 | 不自动重试同一外部操作；尝试按 job/thread 回收结果，否则 hold |
| 内容审核明确拒绝 | 不重复 L1；进入一次 L2，再进入 L3 |
| 本地磁盘/图片合同失败 | 修复前短重试写入；已有可信候选不重新调用模型 |
| 配额耗尽/余额不足 | 标记 provider unavailable，直接 L3 或延后，不让六群重复撞 API |

---

## 7. Scheduler 风险

### 7.1 当前调度能力

- 生成：每日 00:15，`misfire_grace_time=1800`、`coalesce=true`、`max_instances=1`。
- 发送：每分钟第 15 秒扫描，`misfire_grace_time=45`、`coalesce=true`、`max_instances=1`。
- 启动：只补启动当天且未写 `generation_completed_at` 的生成；`skip_email=true`。
- 进程：FastAPI owner 当前 active；外部 Windows 任务被禁用。

### 7.2 Watchdog 最小设计

1. 每次服务启动后和每 10 分钟运行一次只读扫描。
2. 扫描 `[today-29, today]`，按日期从旧到新；期望任务集合由“当日群 manifest”决定，没有 manifest 时用当前启用群并标记推断。
3. 对每个任务按 execution state 分类：
   - `COMPLETE/SENT`：不动作。
   - `WAIT_RETRY` 且到期：从 last checkpoint 续跑。
   - `MISSING`：建立任务并开始 DATA。
   - `HOLD_MANUAL`：只进状态报告。
   - `SEND_RESULT_UNKNOWN`：永久 fail-closed，绝不自动重发。
4. 发送仅在证据明确为“尚未提交”时自动补；跨日发送前必须重新验证群目标和图片合同。
5. 使用有租期的 owner lease，busy 任务写 `next_retry_at`，不能只返回 `already_running` 后消失。
6. Windows 启动机制需满足：非交互登录也可启动、`StartWhenAvailable`、失败重启、明确工作目录、日志目录、与 FastAPI owner 不重复。

### 7.3 上一任务未完成与第二天关系

- 生成/统计可以按旧到新补，避免后一天先完成导致统计和配额竞争失序。
- 微信发送不能简单把所有历史欠账一起重发；应逐条核对 target、提交证据和 send claim。
- 前一天 `HOLD_MANUAL` 不应阻塞第二天生成，但必须持续出现在每日/总览报告，直到人工消歧。
- Provider 全局故障应熔断并让各群进入共享 `next_retry_at`，避免六群独立重试形成风暴。

---

## 8. 幂等性问题

### 8.1 当前幂等边界

| 阶段 | 当前重复执行行为 | 结论 |
|---|---|---|
| 已有 messages.json | 默认复用，不重抓 | 良好 |
| 已到 IMAGE_READY/READY_TO_SEND/SENT | 非 force 跳过生成 | 良好 |
| Prompt 结果未知 | operation claim 阻止盲重试 | 安全但需人工 |
| 生图已启动结果未知 | attempt manifest/hold | 安全但需人工 |
| 文字成功、图片失败 | 可只续图片 | 良好 |
| SENT | send_due 跳过 | 良好 |
| 普通 FAILED | 自动恢复扫描排除 | **不满足无人值守** |
| 批次 partial/failed | 仍可能写 generation_completed_at | **不满足无人值守** |
| 外部成功、本地 finish claim 失败 | 可能 unknown 或状态分叉 | 需强化 |

### 8.2 建议状态模型

不删除现有阶段状态，新增正交字段：

```json
{
  "stage": "IMAGE",
  "stage_status": "PROMPT_READY",
  "execution_state": "WAIT_RETRY",
  "last_successful_checkpoint": "PROMPT_SAVED",
  "input_hash": "sha256:...",
  "attempt_count": 2,
  "retry_budget": 3,
  "next_retry_at": "2026-08-27T01:20:00+08:00",
  "last_error_type": "API_TIMEOUT_PRE_SUBMIT",
  "manual_hold": false,
  "version": 17
}
```

建议执行状态：

- `ACTIVE`
- `WAIT_RETRY`
- `HOLD_MANUAL`
- `COMPLETE`
- `FAILED_FINAL`

建议 checkpoint：

- `TASK_CREATED`
- `MESSAGES_SAVED`
- `RANKING_SAVED`
- `SUMMARY_SAVED`
- `PROMPT_SAVED`
- `IMAGE_CLAIMED`
- `IMAGE_SAVED`
- `TEXT_SEND_CLAIMED`
- `TEXT_SENT_CONFIRMED`
- `IMAGE_SEND_CLAIMED`
- `SENT_CONFIRMED`

每次 attempt 使用 append-only ledger，包含 operation id、开始/结束、输入 hash、外部提交证据、错误分类和产物 hash。状态文件只保存当前快照，ledger 提供审计证据。

---

## 9. 状态恢复问题

### 9.1 七个崩溃窗口 Resume 矩阵

| 崩溃位置 | 当前重启判断 | 当前风险 | 目标恢复行为 |
|---|---|---|---|
| 1. 聊天记录读取后 | 若 messages.json 已原子保存则复用；保存前会重读 | 读取完成但未保存无 checkpoint；普通 FAILED 后不自动补 | 以 `MESSAGES_SAVED` 为边界；保存前可安全重读，保存后只续排行 |
| 2. AI 总结完成后 | 结果已持久化可复用；无可信结果进入 unknown | Provider 已返回但本地未保存时可能人工 hold | operation receipt + SUMMARY_SAVED；未知不盲重试 |
| 3. Prompt 完成后 | prompt 文件/run 状态一致时可进入生图 | 文件与状态提交窗口可能分叉 | 用 prompt hash reconciler 修复 `PROMPT_SAVED` |
| 4. 生图过程中 | 进程树/attempt manifest；已启动未知不重试 | 可能永久 hold | 先按 job/thread 回收；无可信结果保持人工 hold，允许 L3 但不得重复 L1 付费 |
| 5. 图片生成完成但未保存 | 可尝试同线程候选恢复 | 没有候选或归属证据则失败 | 候选携带 job/group/date/prompt hash；可信则原子提升，不可信转 L3/hold |
| 6. 图片保存后未发送 | run 为 IMAGE_READY/READY 可当天续发 | 跨日不扫描；文件/状态可能分叉 | watchdog 识别 IMAGE_SAVED，重新 preflight 后只续发送 |
| 7. 微信已发送但 DB/run 未更新 | unresolved claim 转 unknown 的部分保护 | 无法无人值守判定，盲重发会重复 | 永久 `SEND_RESULT_UNKNOWN`；人工或可验证外部 receipt 消歧，绝不以超时自动重发 |

### 9.2 恢复原则

1. 本地产物恢复依据必须同时包含稳定 task id、日期、群 id、输入 hash 和产物 hash。
2. 外部动作必须区分：未提交、已确认、结果未知。只有“明确未提交”可自动 retry。
3. `SEND_RESULT_UNKNOWN` 的人工 hold 是正确安全边界，不能为了无人值守成功率而删除。
4. `FAILED` 不能继续作为所有失败的单一终态；至少拆为 retryable、manual hold、final。
5. 恢复执行必须针对单群单阶段，不允许默认 force 整日重跑。

---

## 10. 日志与监控问题

### 10.1 建议结构化日志

每条关键事件至少包含：

```json
{
  "timestamp": "2026-08-27T01:03:12.431+08:00",
  "level": "WARNING",
  "run_id": "20260827-auto-01",
  "group_task_id": "group-42:2026-08-27",
  "group_id": 42,
  "report_date": "2026-08-27",
  "stage": "IMAGE_GENERATION",
  "status": "WAIT_RETRY",
  "duration_ms": 60012,
  "attempt": 2,
  "max_attempts": 3,
  "model": "configured-image-model",
  "provider": "codex-imagegen",
  "api_response_code": 503,
  "error_type": "API_5XX",
  "error_summary": "provider temporarily unavailable",
  "next_retry_at": "2026-08-27T01:04:12+08:00"
}
```

禁止记录 API key、Cookie、完整聊天、完整 Prompt、完整模型响应。群名可在本地状态报告展示，集中日志优先使用稳定 group id；错误摘要限长并脱敏。

### 10.2 `runtime/YYYY-MM-DD/status.json` 建议

```json
{
  "schema_version": 1,
  "report_date": "2026-08-27",
  "generated_at": "2026-08-27T09:00:00+08:00",
  "run_id": "20260827-auto-01",
  "expected_groups": 6,
  "complete_groups": 5,
  "held_groups": 1,
  "overall_status": "attention_required",
  "groups": [
    {
      "group_task_id": "group-42:2026-08-27",
      "group_name": "群 A",
      "statistics": "success",
      "summary": "success",
      "prompt": "success",
      "image": "success_after_retry",
      "image_attempts": 2,
      "send": "sent",
      "last_checkpoint": "SENT_CONFIRMED",
      "next_retry_at": null,
      "manual_hold": false,
      "errors": [
        {"stage": "IMAGE_GENERATION", "attempt": 1, "error_type": "API_TIMEOUT_PRE_SUBMIT"}
      ]
    }
  ]
}
```

写入策略：从逐群 run/ledger 只读重建，先写临时文件再原子替换；如果源状态损坏或期望群集合不确定，`overall_status` 必须 degraded/attention_required，不能默认 success。

### 10.3 需要告警的最小条件

- 当日 00:45 仍无 batch/task manifest。
- 任一任务超过 `next_retry_at + grace` 未推进。
- Provider 全局熔断、配额耗尽、磁盘写失败、SQLite locked 超预算。
- 到发送时间后仍未 `SENT`。
- `SEND_RESULT_UNKNOWN`、run/scheduler corruption、批次与逐群状态不一致。
- Scheduler heartbeat 超过 5 分钟、Windows 启动后服务未恢复。

---

## 11. 建议修改架构

### 11.1 最小增量原则

- 保留 FastAPI、APScheduler、DailyPipeline、RunStore 和现有状态名称。
- 不重写框架，不引入分布式队列作为 P0 前置条件。
- 先补正确的任务账本、重试分类、checkpoint 和 watchdog，再考虑 UI/抽象。
- 所有外部动作继续 claim-first、unknown fail-closed。

### 11.2 建议新增的最小模块

| 模块 | 责任 | 是否修改公开 API |
|---|---|---|
| `TaskReconciler` | 从最近 30 天逐群状态计算缺失/重试/hold/complete | 否，可先内部使用 |
| `AttemptLedger` | append-only 记录外部调用和 checkpoint | 否 |
| `RetryPolicy` | 按错误类型给出预算、backoff、next_retry_at | 否 |
| `ImageFallbackService` | L2 安全 Prompt、L3 本地信息图 | 可复用现有生成入口 |
| `DailyStatusWriter` | 原子生成 runtime 状态报告 | 否 |
| `SchedulerLease` | 明确唯一 owner、busy backlog | 否 |

### 11.3 影响—投入矩阵

| 项目 | 影响 | 预计投入 | 优先级 |
|---|---|---:|---|
| 修正 completed marker + retryable state | 极高 | 中 | P0-1 |
| 30 天 watchdog + Windows 启动验证 | 极高 | 中 | P0-2 |
| L2/L3 生图降级 | 极高 | 中 | P0-3 |
| 数据源 retry/受控 fallback | 高 | 中 | P0-4 |
| 发送 claim 严格检查 | 高 | 小 | P1-1 |
| 批次/逐群 reconciler + status.json | 高 | 小-中 | P1-2/P1-9 |
| 发送逐群隔离 | 中高 | 小 | P1-3 |
| Prompt 长度/转义/分类 | 中高 | 中 | P1-5 |
| 图片统一强校验与落盘恢复 | 中高 | 小-中 | P1-6/P1-7 |
| 多进程状态 version/CAS | 中高 | 中 | P1-8 |

### 11.4 不在本轮范围

- 不更换 Web 框架、Scheduler 或数据库。
- 不重写整个 Pipeline，不做纯代码风格重构。
- 不改变公开 API/Schema/状态类型；本报告字段仅是后续建议。
- 不自动解除 `SEND_RESULT_UNKNOWN`。
- 不实际安装 Windows 服务/任务，不部署、不发微信/邮件。
- 不创建技术债工单、不生成 Canvas、不修改测试。

---

## 12. 建议开发顺序

每项都应独立提交、独立故障注入验证；未通过前不并行扩大生产改动。

### P0-1：修正批次完成语义和失败分类

- 引入 invocation completed 与 task terminal 分离。
- 将普通失败分类为 `FAILED_RETRYABLE / HOLD_MANUAL / FAILED_FINAL`（可用正交字段实现，不必替换现有 status）。
- 验收：一次性取数/Prompt timeout 重启后只续失败群。

### P0-2：实现 30 天 TaskReconciler/Watchdog

- 旧到新扫描缺失任务、retry due 和状态分叉。
- 先只补生成；发送补跑必须遵守提交证据。
- 验收：停机 48 小时、错过计划、重复启动、上一日未完成均收敛。

### P0-3：实现生图 Level 2/Level 3

- 先完成确定性 L3，确保外部模型全不可用仍有图。
- 再增加事实保持的 L2 安全化 Prompt。
- 验收：审核拒绝、配额耗尽、连续 5xx 均至少得到 L3 图。

### P0-4：数据源重试、熔断和受控 fallback

- 先做同 provider 明确未提交错误的指数退避。
- 证明备用后端数据合同一致后再启用 fallback。
- 验收：10392 短暂不可用不再让六群永久失败。

### P1-1：严格检查所有发送 claim 更新

- pre-submit 失败禁止外部调用；post-submit 失败进入 unknown。
- 验收：逐更新点 fault injection，零重复发送。

### P1-2：统一图片强校验和落盘 reconciler

- 复用、发送前均执行统一合同；识别可信“文件成功/状态失败”。
- 验收：截断图片拒绝，可信已落盘图不重复生成。

### P1-3：补齐逐群发送/全局前置隔离

- 发送单群异常不阻断其他群；群名同步使用可信缓存降级。
- 验收：首群失败，后五群仍按安全规则推进。

### P1-4：Prompt 长度、安全预检和错误分类

- 硬上限、控制字符转义、事实优先裁剪、429/5xx/timeout 分类。
- 验收：属性测试和超长中文输入不破 Schema。

### P1-5：结构化日志与每日状态报告

- 引入 run_id/group_task_id/attempt/duration/response code；原子日报。
- 验收：无需查看终端即可定位任一群任一阶段。

### P1-6：唯一 Scheduler owner 与跨进程状态协调

- owner lease、busy backlog、version/CAS、固定锁顺序。
- 验收：双进程压力测试无丢任务/死锁/重复外部动作。

### P2-1：稳定并发时序测试

- **问题**：审计早期一次并发时序测试出现 `maximum == 1` 而期望 2，随后完整测试和单文件测试均通过，属于未稳定复现的时序波动。
- **原因/位置**：`tests/test_generation_concurrency.py` 依赖短事件窗口和线程调度。
- **复现**：Windows 负载下循环运行该文件并记录 seed/调度时刻。
- **方案**：改用显式 barrier/可控 clock，保留并发语义断言。
- **风险**：只改测试同步，不能放宽生产并发约束。
- **验证**：循环 100 次零偶发失败，并仍能捕获串行退化。

### P2-2：清理测试/构建警告

- **问题**：后端有 Starlette/httpx 弃用警告；前端 SSR 测试有 `useLayoutEffect does nothing on the server`。
- **原因/位置**：`.venv/Lib/site-packages/fastapi/testclient.py:1`、`frontend/src/components/common/index.tsx:69-128` 及 motion SSR。
- **复现**：运行完整 pytest 与 `npm test`。
- **方案**：在依赖升级窗口处理 TestClient 迁移；前端测试使用合适的客户端环境或避免 SSR 路径触发 layout effect。
- **风险**：依赖升级可能扩大范围，排在稳定性 P0/P1 之后。
- **验证**：测试通过且无对应 warning。

### P2-3：使健康接口表达真实运行能力

- **问题**：`/health` 恒为 OK，`/ready` 不检查 Scheduler heartbeat、WDA、微信或当日任务完成度。
- **原因/位置**：`app/api/system.py:22-98`。
- **复现**：停止 WDA 或让调度长期无 heartbeat，health 仍可 200。
- **方案**：保留 liveness 200，新增/扩展 readiness 和 daily status，明确各检查项，不把外部故障等同进程死亡。
- **风险**：若部署平台把 readiness 失败自动重启，需避免因外部依赖抖动造成重启风暴。
- **验证**：进程活着但依赖失败时 liveness 正常、readiness degraded、日报可诊断。

---

## 附录 A：30 天无人值守仿真

### A.1 方法

- 固定随机种子：`20260827`。
- 时间范围：2026-07-29 至 2026-08-27，共 30 天。
- 群数：当前启用群数量 6；仿真使用匿名稳定 group id，不读取真实聊天内容。
- 真实逻辑：`DailyPipeline.generate_all/send_due`、`RunStore`、`DailyScheduleState`、排行、文件落盘、阶段状态、发送 claim。
- Mock 边界：微信取数、AI Prompt、生图、微信 sender；没有任何真实外部动作。
- 注入：5% 网络 timeout、5% AI Schema 错误、5% 生图失败、3% 图片下载边界失败、3% 微信发送失败、5% 离线生成日、10% 离线发送日、20% 重复启动候选。
- 仿真输出位于 `TemporaryDirectory`，进程退出时删除，未进入仓库。

### A.2 结果

| 指标 | 结果 |
|---|---:|
| 任务单元 | 180 |
| SENT | 130 |
| FAILED | 21 |
| READY_TO_SEND 但以后未扫描 | 17 |
| PENDING/根本未建任务 | 12 |
| 未完成合计 | **50** |
| 重复生图 | 0 |
| 重复文字发送 | 0 |
| 重复图片发送 | 0 |
| 无限 retry | 0 |
| 缺失 scheduler 日期 | 2 |
| batch success / partial | 12 / 16 |

实际命中的外部边界故障：网络 timeout 10 次、AI 错误格式 6 次、生图失败 4 次、图片下载边界失败 1 次、微信发送失败 5 次。离线生成日为 2026-08-20、2026-08-24；离线发送日为 2026-07-30、2026-08-10、2026-08-24、2026-08-25；5 个日期触发了重复启动检查。

解释：

- 无重复发送/生图说明当前终态跳过和 claim 保护有效。
- 无无限 retry 不是“恢复成功”，而是很多失败在第一次正常返回后被 batch completion marker 封存。
- 17 个 READY_TO_SEND 在离线发送日后永远留在历史目录，直接验证了 current-date-only 扫描缺口。
- 12 个 PENDING 来自两天完全离线且无跨日建档/补跑。
- 随机 5% 进程中断条件在该固定种子下没有实际命中，因此“真实 OS kill 后的全链路恢复”本轮未得到统计样本；上文七窗口矩阵来自真实代码与现有测试证据，仍应在整改测试中强制逐点 kill。
- 当前 pipeline 没有独立图片 downloader 接口，因此 3% 下载失败在 image generator 边界注入；这也是可测试性缺口。
- 仿真没有把“外部已提交但结果未知”作为随机故障；现有测试覆盖 unknown hold，本报告不把它误报为无人值守可恢复。

### A.3 判定

以“30 天 × 全群零永久丢失、SENT 后零重复发送、unknown 永不盲重发”为标准：

- 零重复发送：通过。
- unknown fail-closed：静态代码/现有测试通过，仿真未随机命中。
- 零永久丢失：失败，50 个任务未完成。
- 中断自动恢复：证据不足，需整改后强制 kill 测试。
- 单群失败隔离：生成 worker 大部分场景通过；全局前置和发送外层仍有缺口。
- 第二天继续：当天任务可继续，但上一天欠账不会被处理，失败。

---

## 附录 B：验证证据

所有验证均未调用真实 AI、生图、微信或邮件。

| 命令 | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m compileall -q app scripts tests` | exit 0 |
| `.\.venv\Scripts\python.exe -m pytest tests -q` | **597 passed**, 1 warning, 54.95s |
| `.\.venv\Scripts\python.exe -m pytest tests/test_generation_concurrency.py -q` | **5 passed**, 3.12s；本次未复现早期时序失败 |
| `.\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_generation_concurrency.py` | **592 passed**, 1 warning, 42.16s |
| `npm test`（`frontend`） | **5 files / 21 tests passed**, 11.33s；有 React SSR warning |
| `npm run build`（`frontend`） | exit 0，5004 modules，9.68s |

后端 warning 为 Starlette/httpx TestClient 弃用提示；前端 warning 为 SSR 环境中的 `useLayoutEffect`。二者未阻止本次构建，但应按 P2 处理。

测试通过只证明已编码的合同，不证明真实 Provider、真实微信 UI、Windows 重启或 30 天调度可靠；本报告的 P0 结论来自代码、隔离仿真和实时运行状态三类证据的交叉验证。
