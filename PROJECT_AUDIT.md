# GroupBrief V1 全面工程体检

> 审计日期：2026-08-24（Asia/Shanghai）
>
> 审计基线：`cf29e2decf5ae74e5a7037531edb0218dc4bdcea` 与该时点工作树
>
> 审计方式：Codemap + Code Overhaul Full Audit + SonarQube Community 26.8
>
> 原则：只审计，不修改生产代码，不触发真实微信、邮件或收费模型调用

## 0. 审计边界与结论可信度

本报告覆盖 `app/`、`scripts/`、`frontend/src/`、`templates/`、测试、当前 SQLite Schema/聚合数据、当前本地进程与计划任务。Codemap 跟踪 116 个生产文件、28,149 行；SonarQube 分析 120 个文件、23,570 NCLOC。

审计开始时已有 AI 图片主题、Prompt、前端页面和测试改动；这些改动在审计期间由外部流程提交为 `cf29e2d`。本轮没有改动任何生产业务文件。SonarQube 与测试使用审计时点的隔离快照。

以下结论需要明确边界：

- `/api/system/health` 当前返回 `ok`，只证明服务进程可响应，不证明 WDA、Codex、DeepSeek、SMTP、微信桌面链路都健康。
- `output/.scheduler/2026-08-24.json` 记录 `generation_status=success`、6 个群均 `ready_to_send`、`email_status=sent`；这说明本地状态机收口，但不是外部邮箱或微信送达的不可变证明。
- 未读取 `.env` 值或任何密钥。Git 历史扫描覆盖 40 个提交，没有发现常见 Secret 模式或历史跟踪 `.env` 的证据，但这不是专业 Secret Scanner 的绝对保证。
- 本报告将 Sonar 规则命中与人工源码判断分开；不会把规则分数机械等同于真实风险。

---

## 1. Executive Summary

### 1.1 一句话结论

**当前 GroupBrief 属于“可用 V1”，健康度 54 / 100。**

它已经不是 Demo：真实数据接入、群级并发、串行生图、状态持久化、安全发送认领、失败隔离、调度恢复和管理界面都已经形成完整链路；当前后端绑定 `127.0.0.1:8766`、健康接口正常，当日 6 群生成状态已收口。

它还不是“稳定 V1”：数据库已有 192 条失去群组关联的 `group_runs`，6 个历史 `runs` 永久停在 `running`；V1/V2 两套生成、持久化、排行、Prompt、API 和调度语义仍同时可触达；损坏的 `run.json` 或 scheduler 状态会被静默解释为“未开始”；Docker 支持路径会把无认证管理/发送 API 绑定到 `0.0.0.0`；完整测试不是全绿且存在顺序依赖。

### 1.2 为什么现在能够运行

1. `app/main.py:22-45` 在启动时创建目录、初始化 SQLite、执行手写迁移并启动调度器，历史数据库通常能被就地升级。
2. `app/pipeline/daily_pipeline.py:99-199` 对群级工作并发执行并隔离单群异常，不要求一个群失败就终止整批。
3. `app/v2/run_store.py:164-181,248-302` 对单个 `run.json` 使用临时文件替换，并为发送提供 claim、lease、`result_unknown` 与人工 hold。
4. `app/image/codex_generator.py:236-430,705-852` 已有任务级 staging、结构化结果、进程树回收、候选校验、SHA256 与原子提升。
5. `app/providers/history/wechat_mcp.py:56-145` 对 MCP 有 loopback/允许主机、超时、响应大小和 JSON 边界。
6. `app/sender/wechat_native.py` 与 Pipeline 的发送状态偏向 fail-closed：无法确认时进入 unknown/hold，而不是直接报告成功。
7. Python 测试已有 400+ 场景；本轮隔离执行为 413 通过、1 失败，Python statement coverage 约 74.7%。
8. 当前 V2 默认关闭群级微信自动发送，依赖不完整时通常不会在启动阶段直接造成对外误发。

### 1.3 哪些部分只是“目前没出问题”

- 数据关系靠应用代码约定而非 FK/Unique；真实数据库已经出现逻辑孤儿。
- SQLite 写入、`run.json`、scheduler JSON、图片/文本工件不是同一个事务；进程中断可能留下跨存储半成功。
- `schedule_rule`、`summary_model`、`prompt_model`、`provider_preference` 能保存和展示，但 V2 主流程没有完整消费。
- FastAPI 内 APScheduler 与 Windows Task Scheduler 同时存在，当前主要依赖互斥锁和发送 claim 避免重复，而不是单一调度所有权。
- AI/SMTP 的“请求已提交但响应丢失”没有全链路幂等键，重试可能重复调用或重复邮件。
- 前端无自动化测试；真实微信、Codex 生图、SMTP、OCR 发送仍依赖人工实机验收。

---

## 2. 项目架构图

### 2.1 主架构

```text
┌────────────────────────── Frontend ──────────────────────────┐
│ React/Vite Pages ── frontend/src/api.ts ── HTTP/JSON         │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────── FastAPI / Runtime API ───────────────────┐
│ app/main.py + app/api/* + app/config/* + app/core/*          │
└──────────────┬───────────────────────────┬────────────────────┘
               │ V2                        │ V1 兼容链
               ▼                           ▼
┌──────────────────────────┐   ┌───────────────────────────────┐
│ DailyPipeline/Scheduler  │   │ ReportService/Prompt/Handoff  │
│ generate / recover/send  │   │ SQLite Run/GroupRun/Report    │
└──────────────┬───────────┘   └──────────────┬────────────────┘
               │                              │
       ┌───────┼────────┬─────────┬───────────┼─────────┐
       ▼       ▼        ▼         ▼           ▼         ▼
   WDA/MCP  Ranking  AI Prompt  ImageGen   Email      WeChat
   Provider  Engine  Codex/DS   Codex CLI  SMTP       OCR/UI
       │       │        │         │           │         │
       └───────┴────────┴─────────┴───────────┴─────────┘
                               │
                               ▼
            SQLite + output/{group}/{date}/run.json + 工件
```

### 2.2 核心 V2 数据流

```text
群配置/稳定 wechat_group_id
    → WDA/MCP 取消息
    → messages.json
    → 消息归一化/身份聚合
    → RankingEngine → ranking.json / ranking.txt
    → 事件提取/选题/版式/Prompt → image_prompt.txt
    → Codex ImageGen → daily_image.png
    → RunStore 更新 READY_TO_SEND
    → 到点 claim 发送权
    → 精确目标验证
    → 文本 + 图片
    → SENT 或 result_unknown/manual hold
```

### 2.3 状态流

```text
PENDING
  └→ DATA_READY
       └→ RANKING_READY
            └→ PROMPT_READY
                 └→ IMAGE_READY
                      └→ READY_TO_SEND
                           ├→ SENT
                           ├→ FAILED
                           └→ result_unknown / manual hold

任意生成阶段可进入 FAILED；但损坏 run.json 当前可能被重置为新的 PENDING。
```

### 2.4 Codemap 模块健康

Codemap 平均分约 **60.5 / 100（C）**。没有 A/B 模块；5 个 D、7 个 C。

| 模块 | 行数 | 分数 | 主要原因 |
|---|---:|---:|---|
| V1 报告兼容链 | 696 | 52 / D | 假成功、路径边界、双状态 |
| Pipeline 与调度 | 2,160 | 54 / D | God Service、静默回退、双调度 |
| 运行时与 HTTP API | 2,419 | 58 / D | 无认证边界、833 行 V2 API、配置漂移 |
| AI 摘要与提示词 | 3,601 | 58 / D | 多层重试、Prompt 契约散落、God Builder |
| 运维脚本 | 652 | 58 / D | 失败仍退出 0、半安装、只读命令写 DB |
| 图片生成与恢复 | 1,951 | 61 / C | 高复杂度、双认领协议、无锁状态同步 |
| 微信与邮件交付 | 1,725 | 61 / C | SMTP 幂等缺口、Legacy CLI、God Driver |
| 前端界面 | 10,683 | 62 / C | 5,939 行 CSS、God 页面、轮询/N+1 |
| 数据库与运行状态 | 1,125 | 62 / C | 无 FK、双持久化、损坏状态重置 |
| 微信数据接入 | 2,334 | 64 / C | 858 行 Provider、V1/V2 双模型、Mock fallback |
| 排行榜与统计 | 376 | 64 / C | V1/V2 双引擎/双渲染 |
| 前端 API 客户端 | 427 | 72 / C | 双协议、无超时/取消、弱错误类型 |

修改影响最大的路径是：

```text
DailyPipeline
  ├→ RunStore / SQLite
  ├→ WDA/MCP
  ├→ Ranking
  ├→ Prompt/Codex/DeepSeek
  ├→ ImageGen
  ├→ GroupNameSync
  └→ WeChat/Email
```

因此 `daily_pipeline.py` 的任何“简单修改”都需要至少覆盖生成、恢复、并发、状态持久化、发送 claim 和外部结果未知分支。

---

## 3. 项目健康度

### 3.1 总分：54 / 100

| 维度 | 分数 | 依据 |
|---|---:|---|
| 架构合理性 | 5 / 10 | 模块职责大体可识别，但 V1/V2 并存、存储层反向依赖旧服务、核心 God Service 明显 |
| 业务逻辑 | 6 / 10 | 主链可运行并隔离单群失败；群级配置未完全生效、状态与工件可分叉 |
| 代码质量 | 5 / 10 | Codemap 60.5/C；Sonar 295 smells；多个 600–1,300 行核心文件 |
| 数据设计 | 3 / 10 | 192/224 `group_runs` 孤儿、无 FK/复合唯一、6 个永久 running、手写迁移漂移 |
| 稳定性 | 6 / 10 | 有 mutex、claim、hold、staging 和原子替换；损坏状态静默重置、双调度与退出码仍弱 |
| 测试 | 6 / 10 | Python 413 通过、覆盖较广；1 失败且单测顺序依赖，前端/E2E/真实外部边界缺失 |
| 安全性 | 5 / 10 | 当前 loopback、Secret 忽略、MCP allowlist 较好；Docker 无认证暴露、旧文件路径检查和敏感日志有风险 |
| 性能与资源 | 6 / 10 | 当前规模可承受；存在前后端 N+1、轮询、串行图片长尾、多层 AI 重试和 SQLite 写竞争 |
| 可观测性 | 5 / 10 | 有分类日志、run.json、scheduler 状态；部分异常被吞、健康 GET 有副作用、脚本退出码误导 |
| 文档与可维护性 | 7 / 10 | README、架构、CI、恢复文档较多；V1/V2 文档/代码边界仍漂移，缺版本化迁移与决策记录 |

### 3.2 已经可靠的部分

- V2 发送 claim/lease/unknown hold。
- Codex 图片 staging、验证、哈希、进程树回收。
- WDA/MCP 允许主机、超时、响应大小与 JSON 校验。
- 群级失败隔离和全局图片串行队列。
- 当前 SQLite `PRAGMA integrity_check=ok`；没有发现重复非空 `wechat_group_id`、重复 `(run_id, group_id)` 或孤儿 Report。
- 当前服务实际监听 `127.0.0.1:8766`，不是 `0.0.0.0`。
- 前端严格 TypeScript，当前 `npm run build` 通过。

### 3.3 逻辑完整性现状

| 项目 | 结果 |
|---|---:|
| SQLite 物理完整性 | `ok` |
| `group_runs` 总数 | 224 |
| 找不到 `groups.id` 的 `group_runs` | 192 |
| `runs.status=running` 且未结束 | 6 |
| 孤儿 Report | 0 |
| 重复非空 WeChat Group ID | 0 |
| 重复 `(run_id, group_id)` | 0 |
| SQLite `foreign_keys` | 0 |
| SQLite journal mode | `delete` |

物理完整性正常不等于业务一致性正常；当前主要问题正是“数据库文件没坏，但关系语义已丢失”。

---

## 4. SonarQube 客观指标

### 4.1 扫描信息

- SonarQube：Community Build `26.8.0.126808`
- Project Key：`groupbrief-v1-local-audit`
- Analysis ID：`1a07a159-5e1b-41b1-a74b-f68ee6dfe58a`
- CE Task：`02a52972-25c5-4dcc-8493-1bf7f0d9c2dd`
- 状态：`SUCCESS`，0 warning
- 本地 Dashboard：<http://127.0.0.1:9000/dashboard?id=groupbrief-v1-local-audit>

### 4.2 指标

| 指标 | SonarQube 结果 | 人工解释 |
|---|---:|---|
| Bugs | 5 | 0 个已确认高风险；3 个正则分组、1 个变量重赋值、1 个字符串默认排序均偏低价值 |
| Vulnerabilities | 12 | 全部为非安全用途随机数或 fixture MD5，人工判定低价值命中 |
| Security Hotspots | 0 | 不代表安全；真正的 Docker/认证/路径风险由源码审查发现 |
| Code Smells | 295 | 其中复杂度/大模块值得修；大量弃用图标、API 文档、readonly props 属 P3 |
| Duplication | 0.2% | 5 blocks / 68 lines；全局不严重，局部前端最高 9.7% |
| Coverage | 63.9% | Python 约 74.7%，前端 0%，因此总体下降 |
| Cognitive Complexity | 3,703 | 与 Codemap God Service 结论高度一致 |
| Cyclomatic Complexity | 4,368 | 热点集中在 Pipeline、WDA、Codex、Native Sender 和页面 |
| Maintainability Rating | A | 技术债比率仅 0.3%；不应解读为架构健康 |
| Reliability Rating | D | 由规则级 Bug 候选拉低；需人工确认，当前 5 个均未证明真实故障 |
| Security Rating | D | 由 12 个随机数/MD5 命中拉低；与真实安全边界不一致 |
| Technical Debt | 2,141 分钟 | 约 35 小时 41 分；仅是规则修复估算，不包含迁移/数据治理 |
| Technical Debt Ratio | 0.3% | 估算开发成本基数很大，使比例看起来漂亮 |

Sonar Quality Gate 显示 `OK`，但当前门只检查“新问题为 0”，并以同日先前扫描为比较基线。它不能证明项目达到了可发布质量门槛。

### 4.3 最复杂文件

| 文件 | Cognitive | Cyclomatic | Sonar Coverage |
|---|---:|---:|---:|
| `app/pipeline/daily_pipeline.py` | 265 | 212 | 86.8% |
| `app/providers/history/wechat_data_analysis.py` | 222 | 193 | 79.8% |
| `app/image/codex_generator.py` | 213 | 160 | 71.9% |
| `frontend/src/pages/v2/AIImages.tsx` | 162 | 250 | 0% |
| `app/sender/wechat_native.py` | 161 | 178 | 56.0% |
| `scripts/codex_image_automation.py` | 154 | 145 | 68.5% |
| `app/api/v2_ui.py` | 130 | 145 | 51.7% |
| `frontend/src/pages/v2/Archive.tsx` | 109 | 236 | 0% |

### 4.4 最复杂函数/组件

| 位置 | 函数/组件 | Cognitive |
|---|---|---:|
| `app/pipeline/daily_pipeline.py:726` | `_send_one` | 59 |
| `app/pipeline/daily_pipeline.py:295` | `_generate_one` | 45 |
| `app/ai/conversation_segments.py:108` | `segment_messages` | 43 |
| `app/image/codex_generator.py:246` | `_generate_locked` | 42 |
| `frontend/src/pages/v2/AIImages.tsx:130` | `AIImages` | 38 |
| `app/ai/prompt_builder.py:243` | `build` | 37 |
| `app/services/group_name_sync.py:65` | `sync` | 34 |
| `app/services/email_service.py:52` | `build_email` | 33 |
| `app/ai/topic_selection.py:314` | `score_and_select_topics` | 31 |
| `app/providers/history/wechat_data_analysis.py:294` | `_fetch_messages_mcp` | 31 |

### 4.5 重复最严重位置

| 文件 | Duplication |
|---|---:|
| `frontend/src/pages/v2/ChatRecords.tsx` | 9.7% |
| `frontend/src/pages/v2/Tasks.tsx` | 5.4% |
| `frontend/src/pages/v2/Ranking.tsx` | 4.9% |
| `frontend/src/pages/v2/AIImages.tsx` | 2.0% |

这与源码审查发现的 `STATUS_LABELS`、`runKey`、`statusTone`、加载/错误状态重复相符，属于值得处理的局部重复；全局 0.2% 不值得为了评分做大规模抽象。

### 4.6 值得修与低价值规则

**值得修：**

- `python:S3776` / `typescript:S3776`：47 个复杂函数候选，与 Codemap 的 God Component 结论交叉确认。
- CSS contrast 19 处：真实可访问性问题，应在 UI 稳定后按页面修。
- Vite/npm audit：1 high + 1 moderate，属于开发服务器路径遍历/UNC/读取风险，应规划工具链升级。
- 前端 0% coverage、`v2_ui.py` 51.7%、`wechat_native.py` 56%、`wechat_cli.py` 37%：比追求总百分比更有价值。

**低价值或需人工确认：**

- 12 个 Sonar Vulnerability：`random.Random` 用于每日视觉风格与 fixture，MD5 用作假数据 ID，不承担密码学安全。
- 3 个 `_FENCE_RE` 正则分组 Bug：当前表达式意图就是“开头 fenced 或结尾 fenced”，未发现真实错误。
- `messageTypes.sort()`：集合内容是字符串，默认字典序符合当前展示意图。
- 203 个 Phosphor 图标弃用命中：主要是库 API 演进，不能为清零而一次性替换所有图标。
- 53 个 FastAPI `responses` 文档命中、28 个 readonly props、重复主题分类文字：属于 P3 工程卫生。

---

## 5. 验证与测试体系

| 验证 | 结果 | 解释 |
|---|---|---|
| Python compileall | PASS | `app scripts tests` 编译通过 |
| Python 全量测试 + Coverage | 413 passed, 1 failed, 1 warning | 唯一失败是并发耗时 `<0.45s`，实际约 0.488s |
| 失败测试独立运行 | 3/3 失败 | 独立运行变为 `UnboundExecutionError`，证明还存在全局 DB engine/测试顺序依赖 |
| Python statement coverage | 74.7% | 6,481 / 8,671；没有 branch coverage |
| Sonar overall coverage | 63.9% | 包含 0% 前端 |
| Frontend build/typecheck | PASS | `tsc -b && vite build`；4,596 modules |
| Frontend bundle | 451.30 kB JS / 131.50 kB gzip | 单主 chunk；当前可接受但缺 route split |
| Frontend unit/component/E2E | N/A | 未配置 Vitest/Jest/Playwright/Cypress |
| Lint/format | N/A | Python 无 Ruff/Mypy；前端无 ESLint/Prettier |
| `pip check` | PASS | 无破损依赖 |
| `pip-audit` | PASS | 当前解析版本未发现已知 Python 漏洞 |
| `npm audit` | FAIL | 1 high（Vite）+ 1 moderate（esbuild） |
| SonarQube | PASS | 二次扫描成功，Coverage 已实际导入 |

低覆盖重点：

- `app/providers/history/wechat_cli.py` 37%
- `app/api/v2_templates.py` 40%
- `app/api/logs.py` 42%
- `app/api/v2_ui.py` 52%
- `app/sender/wechat_native.py` 56%
- `app/providers/ai/deepseek.py` 62%

完全依赖人工/实机验证的核心流程：

- 真实 WDA/MCP 数据读取与多账号联系人映射。
- Codex CLI 登录环境、长时生图、进程树异常与真实产物归属。
- 微信窗口、DPI、OCR、剪贴板、目标精确匹配、文本/图片实际提交。
- SMTP 服务端“已接收但客户端断连”的结果未知场景。
- Windows 重启、锁屏、休眠、计划任务与 FastAPI 内 scheduler 同时存在时的行为。

---

## 6. 问题优先级与详细问题

### P0-1 Docker 模式无认证暴露管理/发送能力，且旧文件路径边界不足

- **位置：** `docker-compose.yml:15-27`、`Dockerfile:42-43`、`app/main.py:48-66`、`app/api/files.py:19-34`、`app/services/handoff_service.py:34,105`
- **模块：** 运行时/API、V1 报告兼容链
- **来源：** Codemap + Code Overhaul；Sonar 未发现
- **原因：** Docker 使用 `8766:8766` + `APP_HOST=0.0.0.0`；所有设置、生成、发送、邮件、删除/恢复接口无认证。旧文件接口用字符串 `startswith` 做 containment，V1 日期/目录参数校验不足。
- **实际影响：** 若宿主防火墙/路由允许访问，局域网用户可修改路径/Provider、触发生图/发送/邮件或读取越界文件。
- **发生概率：** 当前 Windows 进程绑定 loopback 时低；启用仓库提供的 Docker 路径时中到高。
- **收益 / 成本 / 风险：** 收益极高；成本中；若直接加复杂账号体系风险高，若先限定 loopback + 管理令牌则风险低。
- **Blast Radius：** 全部数据、外部发送、配置、宿主文件边界。
- **推荐：** 先将 Docker 端口显式绑定 `127.0.0.1:8766:8766`，用 `Path.is_relative_to`/等价方式统一 containment；若需要 LAN，增加最小管理令牌和路由权限分级。不要引入企业级 IAM。

### P0-2 历史数据关系已丢失，缺少 FK/Unique 使问题可继续累积

- **位置：** `app/db/models.py:10-79`、`app/db/repository.py:331-425`、`app/api/runs.py:29-37`
- **模块：** 数据库与运行状态
- **来源：** Codemap + Code Overhaul + 真实 SQLite 聚合
- **原因：** `GroupRun.run_id/group_id`、`Report.group_run_id` 无 FK；`wechat_group_id`、`(run_id,group_id)`、`reports.group_run_id` 无业务唯一约束；删除/历史迁移靠应用层约定。
- **实际影响：** 224 条 `group_runs` 中 192 条失去群组关系；历史页面只能回退为 `群 {id}`，统计/恢复可能关联错误。
- **发生概率：** 已发生。
- **收益 / 成本 / 风险：** 收益极高；成本高；直接加约束会因现有脏数据失败，必须先备份、分类和回填。
- **Blast Radius：** 历史报告、统计、恢复、删除策略、未来迁移。
- **推荐：** 单独执行“只读分类 → WAL 感知备份 → 映射/归档孤儿 → 临时表迁移 → FK/Unique → foreign_key_check → 回滚演练”。不得在普通启动中顺手修。

### P1-1 损坏状态被静默重置，可能把“结果未知”解释为“未开始”

- **位置：** `app/v2/run_store.py:153-162,339-364`、`app/scheduler/daily_v2_job.py:40-61`
- **模块：** 持久化、调度
- **来源：** Codemap + Code Overhaul
- **原因：** JSONDecodeError/OSError 后返回新 PENDING/空日状态，而不是 quarantined/corrupt。
- **实际影响：** 已生成、已发邮件或已提交微信但未写完成标记时，重启后可能重新生成或重复外部副作用。
- **发生概率：** 低到中；断电、磁盘错误、跨进程覆盖时升高。
- **收益 / 成本 / 风险：** 收益高；成本中；修复需兼容旧状态文件。
- **Blast Radius：** 单群单日；scheduler 文件损坏时整批。
- **推荐：** 引入 `CORRUPT/RESULT_UNKNOWN` 隔离状态、保留原文件、manifest-last 校验和人工恢复入口；任何外部提交阶段都不得自动回退 PENDING。

### P1-2 V1 状态存在假成功、永久 running 和失败退出码 0

- **位置：** `app/services/report_service.py:130-187,247-273,317`、`app/services/handoff_service.py:52-95`、`scripts/run_daily_pipeline.py:35,99-124`
- **模块：** V1 报告兼容链、运维脚本
- **来源：** Codemap + Code Overhaul + 真实 SQLite
- **原因：** 多阶段 commit；worker 异常新建失败记录而非收口旧记录；成功只看 ranking/prompt 状态，不看工件；CLI 分支无条件返回 0。
- **实际影响：** 当前有 6 个 `runs=running`；文件缺失/部分失败仍可能被监控视为成功。
- **发生概率：** 已发生。
- **收益 / 成本 / 风险：** 收益高；成本中；旧调用方可能依赖当前退出码，需版本化改变。
- **Blast Radius：** V1 API、Windows 发送任务、历史统计。
- **推荐：** 先定义可机读 terminal contract；CLI 对 failed/partial/blocked/already_running 分别返回非零或独立码；为旧 running 提供只读审计和显式归档，不自动猜测成功。

### P1-3 V1/V2 两套仍可触达的业务系统造成状态分叉

- **位置：** `app/services/report_service.py`、`app/pipeline/daily_pipeline.py`、`app/db/models.py:38-79`、`app/v2/run_store.py`、`app/ranking/*`、`app/services/ranking_service.py`、`frontend/src/api.ts:22-365`
- **模块：** 全链路
- **来源：** 多方共同发现
- **原因：** 迭代时保留旧 API/服务/数据库/输出/脚本，同时新 V2 使用独立 run.json 与状态协议。
- **实际影响：** 两个页面/脚本可以对“最新状态”给出不同答案；修一个排行/Prompt/发送问题需要检查两套实现。
- **发生概率：** 高。
- **收益 / 成本 / 风险：** 收益高；成本高；一次性删除风险极高。
- **Blast Radius：** API、前端、数据库、调度、邮件、历史。
- **推荐：** 冻结 V1 新功能，先记录真实调用者与数据保留要求；用适配层只读 V1 历史，新生成只走 V2；最后分轮退役写路径。

### P1-4 群级配置能保存但未完整进入执行路径

- **位置：** `app/db/models.py:18,24-27`、`app/scheduler/period.py:33-47`、`app/pipeline/daily_pipeline.py:124,1045,1115`
- **模块：** Pipeline、设置/API
- **来源：** Codemap + Code Overhaul
- **原因：** UI/DB 先扩展字段，Pipeline 仍使用全局默认或固定 Provider。
- **实际影响：** 用户以为已配置 `schedule_rule/summary_model/prompt_model/provider_preference`，实际运行仍可能走默认值。
- **发生概率：** 高，只要使用这些字段。
- **收益 / 成本 / 风险：** 收益高；成本中；真正启用模型路由可能改变费用与结果，必须显式验收。
- **Blast Radius：** 单群日期窗口、Provider、模型调用。
- **推荐：** 为每个字段建立“保存 → API 回读 → Pipeline 消费 → run.json 审计”的契约测试；未实现字段应在 UI 标为不可用，而不是静默保存。

### P1-5 外部调用重试缺少一致幂等边界

- **位置：** `app/providers/ai/codex.py:123-221`、`app/providers/ai/deepseek.py:143-259`、`app/services/email_service.py:111-243`、`scripts/send_daily_email.py:203-243`
- **模块：** AI、交付
- **来源：** Codemap + Code Overhaul
- **原因：** Builder/Provider 多层重试与 fallback 叠加；SMTP 断线后重发同一消息；没有 request/message idempotency key。
- **实际影响：** Provider 已接收但响应丢失时可能重复计费；SMTP 已接收时可能重复邮件。
- **发生概率：** 中；日志已有连接中断类历史证据。
- **收益 / 成本 / 风险：** 收益高；成本中到高；错误实现幂等可能阻止合法重试。
- **Blast Radius：** 单个 Prompt chunk、单群邮件或整批。
- **推荐：** 合并重试所有权；只对 429/明确 5xx/连接前失败重试；为邮件生成稳定 Message-ID 和本地发送 ledger；结果未知时进入 hold。

### P1-6 真实数据失败可在 V1 静默落入 Mock

- **位置：** `app/providers/history/registry.py:21-47`、`app/services/history_service.py:76-128`
- **模块：** 微信数据接入
- **来源：** Codemap + Code Overhaul
- **原因：** `history_provider_mock_enabled` 时 Mock 自动追加，首个 OK/EMPTY_RESULT 即返回。
- **实际影响：** 真实 WDA/MCP 不可用时，可能生成结构正常但事实不真实的报告。
- **发生概率：** 中；当前数据库设置显示 Mock 可启用，真实 Provider 故障历史存在。
- **收益 / 成本 / 风险：** 收益高；成本低；会减少“可演示性”但提升真实性。
- **Blast Radius：** 对应日期的全部 V1 报告。
- **推荐：** 生产模式 fail closed；Mock 只能由显式开发变量 + fixture 标识启用，所有产物必须携带 `source=mock` 且禁止发送。

### P1-7 双调度入口与误导性退出码削弱无人值守可信度

- **位置：** `app/scheduler/manager.py:36-82`、`scripts/install_daily_task.py:35-71,114`、`scripts/daily_auto.py:69`
- **模块：** 调度、运维脚本
- **来源：** Codemap + Code Overhaul + 当前 Windows 状态
- **原因：** FastAPI 内 APScheduler 和 Windows `GroupBriefDaily/GroupBriefDailySend` 同时存在；`already_running` 和安装失败可返回 0。
- **实际影响：** 任务可能只是被另一实例抢锁，却被监控视为完成；半安装计划任务无法由退出码发现。
- **发生概率：** 中；当前两类调度都存在。
- **收益 / 成本 / 风险：** 收益高；成本低到中；切换调度所有权时需避免漏跑。
- **Blast Radius：** 每日整批生成与发送。
- **推荐：** 每个部署只保留一个 scheduler owner；另一个只做 watchdog；为 `success/partial/blocked/already_running/not_run` 定义稳定退出码与监控事件。

### P1-8 Schema 迁移无版本链且默认值已漂移

- **位置：** `app/db/repository.py:34-207`
- **模块：** 数据库与运行状态
- **来源：** Codemap + Code Overhaul + Schema 对比
- **原因：** `create_all + PRAGMA + ALTER + settings marker` 在启动时执行，多个迁移各自 commit。
- **实际影响：** 当前 SQLite 列默认仍保留旧 DeepSeek/blue_white，而运行数据已使用 Codex/random_preset；多进程或中断可产生部分迁移。
- **发生概率：** 中。
- **收益 / 成本 / 风险：** 收益高；成本中高；贸然引入 Alembic 也可能过度设计。
- **Blast Radius：** 启动、恢复、所有新建群。
- **推荐：** 不必立刻上复杂框架；先建立单一 `schema_version`、顺序迁移注册、每版事务/备份/校验和回滚脚本，再评估 Alembic。

### P2-1 复杂度集中在少数 God Module

- **位置：** `daily_pipeline.py`、`codex_generator.py`、`wechat_data_analysis.py`、`wechat_native.py`、`v2_ui.py`、`AIImages.tsx`、`styles.css`
- **模块：** 多模块
- **来源：** Codemap + Code Overhaul + Sonar
- **原因：** 快速 V1 迭代把协议、状态、I/O、恢复和 UI 交互集中在同一个类/文件。
- **实际影响：** 修改成本和回归范围持续增大；Sonar top function complexity 达 59。
- **发生概率：** 每次维护都发生。
- **收益 / 成本 / 风险：** 收益中高；成本高；无 characterization tests 时拆分风险高。
- **Blast Radius：** 核心生成/发送与管理页面。
- **推荐：** 先补行为网，再按阶段提取纯函数/边界对象；不引入微服务、CQRS、事件溯源或大量模式。

### P2-2 测试存在顺序依赖、时间阈值脆弱和前端/E2E 空白

- **位置：** `tests/conftest.py:13-23`、`tests/test_generation_concurrency.py:147`、`frontend/package.json:6-9`
- **模块：** 测试体系
- **来源：** Code Overhaul + 实测 + Sonar Coverage
- **原因：** 全局 repository engine 由其他测试隐式初始化；并发测试用硬墙钟 `<0.45s`；前端没有测试框架。
- **实际影响：** 全量 413/1 失败，单独运行同一测试 3/3 因 unbound engine 失败；CI 结果可能随顺序/负载变化。
- **发生概率：** 已发生。
- **收益 / 成本 / 风险：** 收益高；成本中；不应通过放宽所有断言掩盖真实并发回归。
- **Blast Radius：** CI 可信度、并发/状态修改安全网。
- **推荐：** 每测试 fixture 显式 init/dispose DB；用事件/barrier 验证并发而非极窄墙钟；先为 AIImages、Archive、Settings 和发送确认补行为测试，再选少量 Playwright 实机前流程。

### P2-3 前后端 N+1、轮询与单主 Bundle

- **位置：** `app/api/runs.py:25-31`、`app/api/reports.py:59-63`、`frontend/src/pages/v2/Tasks.tsx:113`、`AIImages.tsx:376-388`
- **模块：** API、前端
- **来源：** Code Overhaul + Codemap
- **原因：** 每行/每 run 再请求明细；固定 2/5 秒轮询失败后静默继续；所有页面打入单主 JS chunk。
- **实际影响：** 当前小数据影响有限，历史增长后请求数和页面等待线性增长；网络失败会持续轮询。
- **发生概率：** 中。
- **收益 / 成本 / 风险：** 收益中；成本低到中；过早引入复杂缓存风险大。
- **Blast Radius：** Tasks/AIImages/历史列表。
- **推荐：** 先批量查询/分页、AbortController、退避和可见错误；页面级动态 import 可后置。

### P2-4 可观测性存在“看似成功”的空洞

- **位置：** `app/main.py:29-35`、`app/scheduler/send_job.py:12-19`、`app/core/logging.py:68`、`app/api/system.py:23-48`
- **模块：** 运行时、调度
- **来源：** Codemap + Code Overhaul
- **原因：** 启动检查异常被置空；send job 仅日志不返回失败；已有 root handler 时分类日志不初始化；Provider GET 会外呼并写 DB。
- **实际影响：** 服务“活着”但依赖不可用；scheduler 认为任务完成；健康刷新本身消耗资源并扩张 `provider_health`。
- **发生概率：** 中。
- **收益 / 成本 / 风险：** 收益中高；成本中；强制启动门禁可能降低本地可用性。
- **Blast Radius：** 运维判断、监控、Provider 表。
- **推荐：** 区分 liveness/readiness/deep diagnostics；调度器记录终态与退出码；深健康检查显式触发并限频，ProviderHealth 设置 retention。

### P2-5 依赖可复现性和 Vite 开发服务器漏洞

- **位置：** `requirements.txt`、`requirements-dev.txt`、`frontend/package.json`、`.github/workflows/ci.yml`
- **模块：** 工具链
- **来源：** Code Overhaul + `pip-audit` + `npm audit/outdated`
- **原因：** Python 全部使用 `>=` 且无 lock；前端锁定 Vite 5.4.21，审计发现 1 high + 1 moderate；多个前端直接依赖已落后一个或更多 major。
- **实际影响：** Python 重建可能解析到不同组合；Vite dev server 在 Windows/路径场景存在风险。
- **发生概率：** 中。
- **收益 / 成本 / 风险：** 收益中；成本中；直接跳 React 19/Vite 8 有较大兼容风险。
- **Blast Radius：** 本地开发、CI、Docker 构建。
- **推荐：** 先引入 Python constraints/lock 和 Renovate/Dependabot；Vite 单独升级并回归 build/dev；不要把 React major 升级和核心稳定性整改绑在同一轮。

### P2-6 工件写入与目录身份仍可能碰撞

- **位置：** `app/v2/run_store.py:119-121`、`app/services/handoff_service.py:34-92`、`app/pipeline/daily_pipeline.py:433-671`
- **模块：** 持久化、V1 报告、Pipeline
- **来源：** Codemap + Code Overhaul
- **原因：** 目录使用清洗/截断后的群名；多工件顺序写入；V1 handoff 非原子。
- **实际影响：** 同名/清洗后相同群可能覆盖；状态和工件不一致。
- **发生概率：** 当前未发现活动群碰撞，未来中低。
- **收益 / 成本 / 风险：** 收益中；成本中高；改目录会影响历史 URL/归档。
- **Blast Radius：** 单群全部历史工件。
- **推荐：** 新目录加入稳定 group_id，保留旧目录只读映射；用临时执行目录 + manifest-last 原子发布；不得直接批量搬迁现有 output。

### P3 低 ROI 工程卫生

- Phosphor 图标弃用、readonly props、FastAPI `responses` 文档、主题标签常量、少量 wrapper 页面、通知 placeholder、目录命名美化。
- 这些问题可以在相关页面/模块被修改时顺手处理，不应占用 P0/P1 稳定性预算。

---

## 7. 技术债 Top 10

按“风险 × 影响 × 未来维护成本 × 修改收益”排序：

| 排名 | 技术债 | 优先级 | 为什么排在这里 |
|---:|---|---|---|
| 1 | Docker 无认证暴露 + 旧路径边界 | P0 | 可直接扩大为配置、文件和外部发送控制权 |
| 2 | 192 条逻辑孤儿 + 无 FK/Unique | P0 | 已发生的数据关系损失，会继续积累 |
| 3 | 损坏状态静默回退 PENDING | P1 | 可能把结果未知变成重复执行 |
| 4 | V1 假成功/永久 running/退出码 0 | P1 | 已有 6 条卡死记录，破坏监控可信度 |
| 5 | V1/V2 双生成与双状态体系 | P1 | 所有未来功能都要承担双倍维护与状态分叉 |
| 6 | 群级配置未真正路由 | P1 | 用户配置与实际行为不一致，涉及日期和模型费用 |
| 7 | AI/SMTP 重试无统一幂等 | P1 | 可能重复扣费或重复邮件 |
| 8 | 双 scheduler owner + 误导退出码 | P1 | 无人值守可能“没跑却显示成功” |
| 9 | 核心 God Module 与复杂函数 | P2 | 每次修复 Blast Radius 大，长期维护成本复利增长 |
| 10 | 测试顺序依赖 + 前端/E2E 空白 | P2 | 阻碍安全拆分与真实失败场景验证 |

---

## 8. 删除候选清单

本轮只列出，不删除。即使标为“可以安全删除”，也应在独立清理轮次执行 `rg → build/test → diff`。

### 8.1 可以安全删除候选

- `frontend/src/pages/v2/History.tsx`：仅包装 `Archive`，导航已归一到 archive，未发现引用。
- `frontend/src/pages/v2/System.tsx`：仅包装 `Settings`，导航已归一到 settings，未发现引用。
- `app/providers/v2/base.py`：自称预留接口，全仓未发现实际引用。

### 8.2 需要确认后删除

- `app/scheduler/generate_job.py`、`app/scheduler/email_job.py`：当前 manager 不注册，但外部脚本可能仍调用。
- `app/services/prompt_service.py:_build_context_text`：旧调用兼容候选。
- `app/providers/history/wechat_cli.py`：V1 registry/配置仍可能路由。
- `scripts/codex_image_automation.py`、`docs/CODEX_IMAGE_AUTOMATION_PROMPT.md`：旧桌面流程，仍可能用于人工恢复。
- `scripts/test_wechat_send.py`：固定 legacy sender，作为测试价值低，但可能是现场手工工具。
- V1 `ReportService/RankingService/HandoffService` 与旧文件 API：必须先证明没有真实调用者并迁移历史读取。
- `archive-legacy` CSS 与其它旧页面样式：必须先做实际 DOM/CSS coverage，不能凭名称删除。
- 无引用的 notification 按钮：删除会改变 UI，不属于纯 dead code。

### 8.3 暂时不要删除

- Mock Provider 与 `fixtures/`：Python 测试和本地开发仍使用；应限制生产路由，不是直接删除。
- 所有 migration marker/旧列兼容：尚未证明所有数据库都完成升级。
- V1 SQLite 表与历史记录：即使停止写入，也需保留只读历史迁移期。
- `top10_lines`、`deepseek_ms`、旧模型字段等兼容数据：先建立版本边界和读取统计。
- 发送 claim/lease/hold、Codex staging/recovery、WDA allowlist：这些是当前可靠性保护。

---

## 9. 暂时不要动的地方

以下代码可能不漂亮，但运行价值高、修改收益低于风险：

1. `app/v2/run_store.py:248-302` 的发送 claim、lease、`result_unknown` 和人工 hold。
2. `app/pipeline/daily_pipeline.py:726-1010` 的发送前认领、提交后未知结果和 fail-closed 分支。
3. `app/image/codex_generator.py:42-140,236-430,705-852` 的 Windows 进程树终止、跨进程互斥、staging、候选归属和原子提升。
4. `app/providers/history/wechat_mcp.py:56-145` 的 loopback/allowed-host、超时、响应上限和 JSON 校验。
5. `app/pipeline/daily_pipeline.py:368-388` 对消息快照无效时拒绝静默回源。
6. `app/services/group_name_sync.py` 基于稳定 WeChat ID 同步群名并保留人工 `send_target` 的逻辑。
7. Docker 单 worker 与全局 generation mutex。它们不是最终并发方案，但当前保护 SQLite 和外部副作用。
8. 数据库设置覆盖 `.env` 的既有行为。来源层次需要清晰化，但不能直接反转，否则会改变生产配置。
9. 图片全局串行队列。它会拉长总耗时，但符合当前 Codex/桌面资源约束；在有性能证据前不要盲目并行。

---

## 10. Code Overhaul 影响/成本矩阵

| | 低成本 | 高成本 |
|---|---|---|
| **高影响** | Docker loopback、Path containment、非法设置拒绝、脚本退出码、Mock 生产禁用 | 孤儿数据治理/FK、V1/V2 收敛、稳定 group_id 目录、幂等 ledger |
| **低影响** | CSS contrast、客户端超时/取消、错误类型、少量 dead wrapper | 全量图标替换、为清零重复率抽象、全面 CSS 重写、框架 major 迁移 |

### 当前已有、应复用的能力

- `RunStore` 原子 JSON 替换和发送 lease。
- `generation_mutex` / 单 worker。
- `verify_image`、Codex staging/manifest/hash；应补强而不是重写。
- MCP allowed-host/timeout/size guard。
- `GroupNameSyncService` 稳定身份语义。
- `tests/conftest.py` 已有外部调用隔离意图；需改为每测试独立 DB 生命周期。
- GitHub Actions 已有 Windows Python test + frontend build 基线。

### 本轮不在范围

- 不重构、不修 Sonar、不加 Schema、不删代码、不升级依赖。
- 不创建 Beads；用户要求报告完成后停止。
- 不执行真实微信、邮件、WDA、Codex、DeepSeek 验收。
- 不改变部署、计划任务或当前运行进程。

---

## 11. 整改路线图

每轮都必须小范围、可独立测试、可独立提交、可回滚；禁止“全面重构”。

### P0.1 网络与文件安全边界

- **范围：** Docker loopback、最小管理令牌（如需要 LAN）、统一 `Path` containment、V1 日期/文件 allowlist。
- **测试：** API auth/unauth、路径 traversal 参数化测试、Windows 路径变体、Docker 端口检查。
- **回滚：** 单独配置/路由提交；保留 loopback 本地开发模式。

### P0.2 数据一致性治理

- **范围：** 只读分类 192 条孤儿、备份/恢复演练、映射策略、FK/Unique 迁移设计。
- **测试：** `integrity_check`、`foreign_key_check`、重复约束、旧库升级、回滚恢复。
- **回滚：** 原库只读保留；迁移在副本验证后替换。

### P1.1 损坏状态与结果未知恢复

- **范围：** run/scheduler JSON schema、CORRUPT 隔离、manifest-last、人工恢复。
- **测试：** 截断 JSON、空文件、半写、重启、邮件/微信已提交但无完成标记。
- **回滚：** 新读取器兼容旧格式；不批量改历史文件。

### P1.2 单一调度所有权与退出码

- **范围：** FastAPI scheduler 与 Windows task 角色、稳定终态/退出码、安装事务。
- **测试：** 双实例抢锁、already_running、partial、blocked、半安装、系统重启。
- **回滚：** 保留另一入口为禁用的 watchdog，不同时启用。

### P1.3 配置契约与 Provider 真实性

- **范围：** `schedule_rule/model/provider_preference` 真正消费或 UI 禁用；生产 Mock fail closed。
- **测试：** 保存/回读/执行/run.json 审计；真实失败不得落 Mock。
- **回滚：** 每字段 feature gate；默认保持现行为直到测试通过。

### P1.4 AI 与邮件幂等

- **范围：** 单层 retry policy、错误分类、请求/邮件稳定 ID、发送 ledger、unknown hold。
- **测试：** 429、500、连接前断、提交后断、重复进程、SMTP 已接收无响应。
- **回滚：** 幂等表/文件独立；关闭新策略可回到现有 fail behavior。

### P1.5 V1 冻结与退役计划

- **范围：** 调用者清单、只读历史适配、新写入只走 V2、逐路由退役。
- **测试：** 历史页面、导出、V1/V2 同日对照、旧脚本使用统计。
- **回滚：** 每条路由独立 feature flag；不删除历史表/工件。

### P2.1 测试隔离与关键行为网

- **范围：** 每测试临时 DB/engine、并发 barrier、前端核心行为、少量 E2E。
- **测试：** 单文件、随机顺序、重复运行、coverage + 非 coverage 对照。
- **回滚：** 测试提交独立于生产重构。

### P2.2 Pipeline 阶段拆分

- **范围：** 先提取纯状态转移/结果分类，再分离生成、图片、发送协调；保留现有 Facade。
- **测试：** 以 P2.1 characterization 为门；原 API 与 run.json 不变。
- **回滚：** Facade 可切回旧内部实现。

### P2.3 API/前端热点拆分

- **范围：** `v2_ui.py` 按领域拆 router；`AIImages` 提取数据 hook/命令 hook；共享状态展示 helper；CSS 按页面渐进拆。
- **测试：** API contract、组件行为、截图/可访问性。
- **回滚：** 路由路径和页面对外契约不变。

### P2.4 依赖、可观测性与性能小步改进

- **范围：** Python lock/constraints、Vite 安全升级、liveness/readiness、Provider retention、批量查询/分页/退避。
- **测试：** clean install、Docker build、bundle、API 查询数、健康检查无副作用。
- **回滚：** 每一项独立提交，不与 React major 或核心重构捆绑。

---

## 12. 最终判断

这个项目现在能跑，不是纯靠运气。它已经有一套相当有价值的“外部副作用安全骨架”：发送认领、结果未知 hold、Codex staging、图片归属验证、MCP 边界、群级失败隔离。这些部分是 V1 最可靠、最不应该为了工程美学重写的资产。

项目目前最大的风险也不是“代码不够高级”，而是三个现实问题：

1. **数据关系和状态真相不唯一。** SQLite 与 run.json、V1 与 V2、工件与状态之间仍可能分叉。
2. **部署安全边界依赖环境约定。** Windows loopback 安全，但仓库提供的 Docker 路径会扩大暴露面。
3. **测试和监控还不能完全证明无人值守。** 一个完整测试失败、单独运行又暴露顺序依赖；前端和真实外部边界没有自动化网。

最安全的提升顺序不是拆大文件，而是：

```text
先封网络/文件边界
→ 再保护和修复数据真相
→ 再让损坏/未知状态 fail closed
→ 再统一调度、退出码、配置与幂等
→ 最后用测试网逐步收敛 V1/V2 和拆分 God Module
```

在完成 P0/P1 之前，不建议进行全面架构重写、React major 升级、CSS 全量重构或为了 Sonar A 评级批量改规则问题。

## 13. 尚待用户决策

1. 正式部署是否永远只允许本机访问，还是未来需要 LAN/远程访问？这决定认证边界。
2. V1 API/数据库历史是否仍有实际用户或外部脚本依赖？这决定退役速度。
3. 192 条孤儿 `group_runs` 是保留审计、映射回群、匿名归档还是删除候选？必须由业务决定。
4. 群级模型/Provider 字段是计划真实启用，还是应从 V1 UI 暂时隐藏？
5. FastAPI APScheduler 与 Windows Task Scheduler 哪一个是唯一生产 owner？
6. 外部邮件/微信需要怎样的不可变送达证据：Message-ID、日志、截图还是人工确认？

审计到此停止。等待确认整改路线图后，再进入任何代码或数据修改。
