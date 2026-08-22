# GroupBrief V2 架构文档

> 状态：P0 基线固化
> 日期：2026-08-18
> 历史依据：[`development-history/v2-roadmap.md`](development-history/v2-roadmap.md)（P0～P9 严格串行推进）

---

## 一、V2 与 V1 的关系

GroupBrief V2 不是推翻 V1，而是在 V1 已验证能力之上重构出「全自动微信群日报发布系统」。

| 能力 | V1 | V2 |
| --- | --- | --- |
| 聊天数据来源 | WeChatDataAnalysis MCP / 导出 / Mock 手动降级 | WeChatDataAnalysis 本地数据源自动取数 |
| 排行榜 | 代码确定性统计（已有） | 保留，改为模板化渲染 |
| DeepSeek 生图 Prompt | 已有（V4 Flash） | 保留，Prompt 模板化 |
| 图片生成 | 无（人工交给 GPT 生图） | Codex `$imagegen` 自动生图 + 落盘 |
| 发布渠道 | 邮件（每天一封） | 微信群（排行榜文字 + 图片，各 1 条） |
| 输出目录 | `output/YYYY-MM-DD/{群}/` | `output/{群}/YYYY-MM-DD/` |
| 前端 | 仪表盘 / 群管理 / 记录 / 文件 / 日志 | Dashboard / 群管理 / 模板中心 / 历史日报 / 系统状态 |

V2 明确不做的能力（沿用路线文档）：实时监听、AI 实时回复、群机器人互动、
一句话日报、文字摘要单独发送、锁屏发送、复杂重试、多渠道发送、多账号、
服务器部署。

---

## 二、V1 审计结论（P0 已完成）

### 2.1 V1 可直接复用的模块

| 模块 | 路径 | 复用说明 |
| --- | --- | --- |
| MCP 客户端 | `app/providers/history/wechat_mcp.py` | JSON-RPC/MCP 客户端（仅本机回环、token 不泄露），V2 数据源直接复用 |
| WeChatDataAnalysis 读取 | `app/providers/history/wechat_data_analysis.py` | health_check / list_sessions / resolve_session / 锚点翻页消息读取，V2 数据源适配层包装它 |
| 联系人解析 | `app/providers/history/contact_resolver.py` | 微信号 → 真实显示名，V2 数据源继续使用 |
| 消息标准化 | `app/services/message_normalizer.py` | RawMessage → NormalizedMessage（系统消息过滤 / 可计类型 / ai_text），V2 RankingEngine 直接使用 |
| 排行榜统计 | `app/services/ranking_service.py` | 确定性统计（总数 / 人数 / Top10），V2 在其上增加模板渲染，不改变数字逻辑 |
| DeepSeek 调用 | `app/providers/ai/deepseek.py` | V4 Flash 分块分析 + 合并，V2 ImagePromptBuilder 复用底层 `_chat` |
| 分类日志 | `app/core/logging.py` | 全项目日志基础设施 |
| 配置系统 | `app/config/settings.py` | pydantic-settings + 数据库运行时设置 |
| SQLite 持久化 | `app/db/` | Group / Run / Report 等模型与 Repository，V2 群配置扩展字段后继续使用 |
| 调度框架 | `app/scheduler/manager.py` | APScheduler BackgroundScheduler，V2 扩展新任务与 per-group send_time |

### 2.2 V2 必须新增或重构的模块

| 模块 | 用途 | 对应轮次 |
| --- | --- | --- |
| `app/data_sources/base.py` | WeChatDataSource 抽象 + Message Schema | P1 |
| `app/data_sources/wechat_data_analysis.py` | WeChatDataAnalysisSource 实现 | P1 |
| `app/scheduler/period.py` | PeriodResolver（每天统计前一自然日） | P2 |
| `app/ranking/engine.py` | V2 RankingEngine（输出 ranking.json） | P2 |
| `app/ranking/renderer.py` | RankingRenderer（模板渲染 ranking.txt） | P3 |
| `templates/ranking/` | 排行榜模板资产 | P3 |
| `app/ai/prompt_builder.py` | ImagePromptBuilder（模板化生图 Prompt） | P4 |
| `templates/image_prompt/` | 生图 Prompt 模板资产 | P4 |
| `app/image/image_task.py` | Codex `$imagegen` 串行生图 + 落盘 | P5 |
| `app/sender/base.py` | WechatSender 抽象 | P6 |
| `app/sender/wechat_automation.py` | WechatAutomationSender 实现 | P6 |
| `app/pipeline/daily_pipeline.py` | DailyPipeline（组装 P1-P6 + 发送） | P7 |
| `app/v2/constants.py` | V2 状态机 / 错误类型 / run.json 约定 | P0（接口约定） |
| V2 前端 | Dashboard / 群管理 / 模板中心 / 历史日报 / 系统状态 | P8 |
| 稳定性 | 启动检查 / 日志轮转 / 状态恢复 / 开机自启 | P9 |

---

## 三、V2 总体架构

```text
                         GroupBrief V2
                               │
                               ▼
                     ┌──────────────────┐
                     │    Scheduler     │
                    │  每日 00:15 启动   │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │   Group Config   │
                     │ 读取启用的群与规则  │
                     └────────┬─────────┘
                              │
                              ▼
                ┌─────────────────────────────┐
                │  WeChatDataSource (V2 适配层) │
                │  → WeChatDataAnalysisSource   │
                └──────────────┬──────────────┘
                               │
                               ▼
                    指定群 + 指定时间段消息（Message Schema）
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
        ┌──────────────────┐      ┌──────────────────┐
        │ Ranking Engine   │      │ ImagePromptBuilder│
        │ 代码确定性统计     │      │ DeepSeek V4 Flash │
        └────────┬─────────┘      └────────┬─────────┘
                 │                         │
                 ▼                         ▼
           ranking.json              image_prompt.txt
                 │                         │
                 │          ┌──────────────┴──────────────┐
                 │          ▼                             ▼
                 │   (P3) RankingRenderer           (P5) Codex $imagegen
                 │   → ranking.txt                 → daily_image.png
                 │                                        │
                 └─────────────────────────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │  WechatSender    │
                    │ Windows UI Auto  │
                    └────────┬─────────┘
                             ▼
                        指定微信群（rank 文字 + 图片）
```

数据流分两个阶段（P7 DailyPipeline 实现）：

- **生成阶段（默认 00:15）**：取前一自然日数据 → 存 messages.json → 排行 → ranking.json →
  ranking.txt（模板）→ DeepSeek → image_prompt.txt → Codex 串行生图 →
  daily_image.png → 状态 READY_TO_SEND
- **发送阶段（默认 08:30 起，每群 send_time）**：按群顺序发排行榜文字 → 发图片 → 状态 SENT

---

## 四、V2 状态机（run.json 约定）

每个群每次运行生成一个 `run.json`（输出目录 `output/{群}/{日期}/`）。

```text
PENDING
   ↓ 数据读取成功
DATA_READY
   ↓ 排行成功
RANKING_READY
   ↓ Prompt 成功
PROMPT_READY
   ↓ 生图成功（image_enabled=true 时）
IMAGE_READY
   ↓ 发送就绪
READY_TO_SEND
   ↓ 发送完成
SENT

任何阶段失败 → FAILED（记录 failed_stage + error）
```

常量定义见 `app/v2/constants.py`。

---

## 五、V2 错误类型

```text
WECHAT_DATA_UNAVAILABLE
GROUP_NOT_FOUND
MESSAGE_FETCH_FAILED
RANKING_FAILED
DEEPSEEK_FAILED
PROMPT_FAILED
IMAGE_GENERATION_FAILED
IMAGE_FILE_MISSING
WECHAT_OFFLINE
SEND_TEXT_FAILED
SEND_IMAGE_FAILED
```

失败策略：记录日志 → 标记 FAILED → 停止该群后续步骤 → 继续处理其他群。

---

## 六、V2 统计周期规则（PeriodResolver）

| 执行日 | 统计范围 |
| --- | --- |
| 周一至周日 | 前一自然日 00:00:00 ～ 23:59:59 |

例如 8 月 22 日 00:15 执行时，统计 8 月 21 日全天；周末也运行，周一不再合并周五至周日。

---

## 七、V2 群配置结构

沿用 SQLite `groups` 表并扩展字段（V1 已有字段 + V2 新增字段）：

```text
id, display_name, wechat_group_id, wechat_group_name, enabled,
created_at, updated_at,
+ schedule_rule, send_time, summary_model, prompt_model,
  image_enabled, send_target, ranking_template, image_prompt_template
```

`config/groups.example.yaml` 提供与路线文档一致的配置示例（前端编辑后落库）。

---

## 八、V2 目录结构

```text
GroupBrief/
│
├─ app/
│  ├─ v2/constants.py            # 状态机 / 错误类型（接口约定）
│  ├─ data_sources/              # P1：WeChatDataSource 适配层
│  │  └─ wechat_data_analysis.py
│  ├─ ranking/                   # P2：engine.py / P3：renderer.py
│  ├─ ai/                        # P4：prompt_builder.py
│  ├─ image/                     # P5：image_task.py
│  ├─ sender/                    # P6：base.py / wechat_automation.py
│  ├─ pipeline/                  # P7：daily_pipeline.py
│  ├─ scheduler/                 # V1 已有；P2 新增 period.py
│  ├─ providers/  services/  db/ # V1 复用（见 §2.1）
│  └─ ...
│
├─ config/
│  ├─ groups.example.yaml
│  └─ app.example.yaml
│
├─ templates/
│  ├─ ranking/default.txt        # P3
│  └─ image_prompt/default.md    # P4
│
├─ output/{群名称}/{日期}/        # V2 输出（messages.json / ranking.json /
│                                #   ranking.txt / image_prompt.txt /
│                                #   daily_image.png / run.json）
├─ scripts/
│  ├─ run_daily_pipeline.py      # P7 统一入口
│  ├─ test_wechat_data.py        # P1
│  ├─ test_image_generation.py   # P5
│  └─ test_wechat_send.py        # P6
├─ logs/
├─ frontend/                     # P8 重构
└─ tests/
```

---

## 九、P1～P9 推进顺序

```text
P0 基线固化（本文档 + 接口骨架）
P1 WeChatDataSource（真实验证 MCP 取数）
P2 PeriodResolver + RankingEngine
P3 排行榜模板系统
P4 DeepSeek 生图 Prompt 模板化
P5 Codex $imagegen 串行生图 + 落盘
P6 微信发送 Adapter
P7 DailyPipeline + Scheduler
P8 V2 前端重构
P9 无人值守稳定性
```

---

## 十、外部依赖与验证状态（P0 时点）

| 依赖 | 环境状态 | 验证轮次 |
| --- | --- | --- |
| WeChatDataAnalysis MCP（127.0.0.1:10392） | ✅ 运行中，数据库已存 token | P1 |
| 微信 PC 客户端 | ✅ Weixin.exe 已登录 | P6 |
| DeepSeek V4 Flash | ✅ API Key 已存数据库 | P4 |
| Codex `$imagegen` | ⚠️ `codex` 命令不在 PATH；`~/.codex/` 存在且有历史生成图片 | P5 |
| wechat-automation-api | 待评估 | P6 |
| Windows 长期开机 / 不锁屏 | 环境要求 | P9 |
