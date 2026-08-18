# GroupBrief V2 DEVELOPMENT_LOG

> 开发模型：DeepSeek V4 Flash（AI Prompt 生成）、Claude Code（开发）
> 依据：`GroupBrief_V2_Development_Roadmap.md`（P0 → P9 严格串行）

---

## P0 — V2 基线固化与工程整理（2026-08-18）

### 状态：P0 PASS

### 做了什么
- 审计现有 V1 结构（FastAPI + SQLite + WeChatDataAnalysis MCP 读取 + DeepSeek + 邮件）
- 创建 V2 架构文档 `docs/V2_ARCHITECTURE.md`
- 建立 V2 目录骨架与接口预留：`app/v2/constants.py`（状态机/错误类型/run.json 约定）、
  `app/data_sources/base.py`（WeChatDataSource 抽象 + V2 Message Schema）、
  `app/ranking/engine.py`、`app/ai/prompt_builder.py`、`app/image/image_task.py`、
  `app/sender/base.py`、`app/pipeline/daily_pipeline.py`
- 新增 `config/groups.example.yaml`、`config/app.example.yaml`
- 新增 `templates/ranking/default.txt`、`templates/image_prompt/default.md`
- 清理临时探查脚本 `scripts/tmp_probe_mcp.py`

### 测试结果
- V1 全量 pytest 104 passed，未破坏现有能力
- 所有 V2 接口可导入

### 验收标准
- ✅ V1 现有流程仍能运行
- ✅ 已创建 V2 架构文档
- ✅ 已建立 Adapter / Pipeline 等基础目录
- ✅ 无真实外部发送行为
- ✅ Git 工作区干净
- ✅ 独立 P0 commit：`5b94840`

---

## P1 — WeChatDataAnalysis 数据接入（2026-08-18）

### 状态：P1 PASS（真实 MCP 验证通过）

### 做了什么
- 新建 `app/data_sources/wechat_data_analysis.py`：`WeChatDataAnalysisSource` 实现
  `WeChatDataSource` 接口（health_check / list_groups / resolve_group / fetch_messages），
  复用 V1 `WeChatDataAnalysisProvider` 的本地 MCP 读取（锚点翻页），不重复实现微信数据解密
- 新增 V2 Message Schema 转换：RawMessage → V2Message（message_id / group_id / group_name /
  sender_id / sender_name / timestamp / message_type / content / raw）
- 空结果时校验群是否存在，不存在的群返回 GROUP_NOT_FOUND（否则 EMPTY_RESULT）
- 失败输出 V2 错误类型（WECHAT_DATA_UNAVAILABLE / GROUP_NOT_FOUND / MESSAGE_FETCH_FAILED）
- 新增 `scripts/test_wechat_data.py`：health / list-groups / resolve / fetch / all
- 新增单测 `tests/test_v2_data_source.py`（11 项，注入 FakeProvider 隔离）

### 真实验证结果（output/test-data/）

| 验证项 | 结果 |
| --- | --- |
| health_check | ✅ OK（本地 WeChatDataAnalysis 服务可用） |
| list_groups | ✅ 发现 61 个真实群，中文/Emoji 群名正常 |
| resolve_group("茶馆") | ✅ 精确匹配「茶馆V3.0（三周年纪念）🐮🐴」48643066777@chatroom |
| fetch 茶馆 08-17 全天 | ✅ 415 条，联系人名正确（停用94/罗斯78/啊菌菌阿菌53…），时间过滤正确 |
| fetch 茶馆 08-15~08-17（三天窗口） | ✅ 560 条，跨天窗口正确，V2 周一三天规则取数可用 |
| 不存在的群 | ✅ GROUP_NOT_FOUND / GROUP_NOT_FOUND |
| 存在群但时段无消息 | ✅ EMPTY_RESULT（正常空窗口） |

### 实际使用的 WeChatDataAnalysis 接口
本地 MCP（JSON-RPC）：`wechat.core.get_status`、`wechat.chat.list_sessions`、
`wechat.chat.resolve_session`、`wechat.chat.get_message_anchor`、
`wechat.chat.get_message_around`（`source=auto`）。

### 数据字段映射关系
| V2 Message Schema | 上游字段 |
| --- | --- |
| message_id | id / messageId（source_message_id） |
| sender_id | senderUsername / sender_username / sender / fromUser |
| sender_name | senderDisplayName → ContactResolver 联系人表修正 |
| timestamp | createTime（秒/毫秒 → Asia/Shanghai naive） |
| message_type | renderType / render_type（映射表） |
| content | content |

### 测试结果
- pytest 115 passed（新增 11）
- 真实读取只调用只读 MCP 接口，不修改微信数据

### 验收标准
- ✅ WeChatDataAnalysis 正常时可获取测试群聊天
- ✅ 时间筛选正确（单天/三天）
- ✅ 群映射正确
- ✅ Emoji / 中文昵称不乱码
- ✅ 数据失败有明确错误类型与日志
- ✅ 不修改源数据库

### Commit
- `(待提交)` P1

---

## P2 — 统计周期引擎 + 排行榜统计（2026-08-18）

### 状态：P2 PASS

### 做了什么
- 新建 `app/scheduler/period.py`：`PeriodResolver`（V2 规则）
  - 周一：周五 00:00:00 ～ 周日 23:59:59（**三天**，V1 是两天）
  - 周二~周五：前一天
  - 周六 / 周日：不生成
  - 支持 `schedule_rule` 参数（当前 weekday_default，预留扩展）
  - 统计终点精确到秒（23:59:59，不含微秒）
- 实现 `app/ranking/engine.py`：V2 `RankingEngine`
  - 确定性统计：总消息数 / 发言人数 / Top10
  - 排序：消息数降序，同数量按发送者名称稳定升序
  - 系统消息过滤复用 V1 规则（message_type=system 或系统内容关键词）
  - 输出结构化 `RankingResult.to_dict()`（即 ranking.json）
- 新增 `app/ranking/engine_types.py`：RankingResult / TopSpeaker 数据结构（供 engine 与 renderer 共用）
- 新增 `app/ranking/renderer.py`：最简 ranking.txt（P2 临时格式，P3 模板化）
- 新增单测 `tests/test_v2_period.py`（8 项）与 `tests/test_v2_ranking.py`（8 项）

### 真实测试输出（茶馆群 08-17，415 条真实消息）
ranking.json / ranking.txt 保存于 `output/test-data/`。
统计结果：发言人数 27、总消息 409、Top10 与路线文档示例**完全一致**
（停用94 / 罗斯78 / 啊菌菌阿菌53 / 杯面大英雄39 / 一颗苹果35 /
春夏秋冬18 / 梓木18 / 大明同学17 / 吉米多的围棋7 / 神奇小郭7）。

### 测试结果
- pytest 131 passed（新增 16）

### 验收标准
- ✅ 周一正确统计三天（周五+周六+周日）
- ✅ 周六周日正确跳过
- ✅ Top10 数量正确
- ✅ 中文、Emoji 昵称正常
- ✅ 同一次输入输出完全一致（确定性）
- ✅ 不使用 AI 计算任何排行榜数字

### Commit
- `(待提交)` P2

---

## P3 — 排行榜模板系统（2026-08-18）

### 状态：P3 PASS

### 做了什么
- `templates/ranking/` 模板目录 + `default.txt` 默认模板（群名原样渲染，不硬编码 emoji，
  避免真实群名自带 emoji 导致「🐮🐴🐮🐴」重复；装饰可在模板中心自行编辑）
- `app/ranking/template_service.py`：`RankingTemplateService`
  - 模板 CRUD（list/read/save/delete）、恢复默认、默认模板不可删除、安全文件名校验
  - 默认模板内容固化为 `DEFAULT_RANKING_TEMPLATE` 常量，`validate_template` 校验未支持变量
- `app/ranking/renderer.py` 重构为模板驱动：`RankingRenderer.render(result, template_name)`
  支持变量：group_name / period_start / period_end / speaker_count / message_count / top10_lines
- Group 模型扩展 V2 字段（schedule_rule / send_time / summary_model / prompt_model /
  image_enabled / send_target / ranking_template / image_prompt_template）+ 幂等数据库迁移
  （`ALTER TABLE groups ADD COLUMN`，列不存在才加）
- 模板 CRUD API：`/api/v2/templates/ranking`（列表/读取/保存/恢复默认/删除/预览）
- 新增单测 `tests/test_v2_ranking_template.py`（14 项）

### 模板变量
| 变量 | 说明 |
| --- | --- |
| `{{group_name}}` | 群名称 |
| `{{period_start}}` / `{{period_end}}` | 统计起止时间 |
| `{{speaker_count}}` | 发言人数 |
| `{{message_count}}` | 总消息数 |
| `{{top10_lines}}` | Top10 多行（`1.名称【数量】`） |

### 示例渲染结果（真实茶馆数据）
`===== 茶馆V3.0（三周年纪念）🐮🐴 =====`（emoji 单次，与路线文档 §三 一致）

### 测试结果
- pytest 145 passed（新增 14）
- API 冒烟：GET 列表 / GET 内容 / POST 预览 均 200

### 验收标准
- ✅ templates/ranking/ 已创建
- ✅ RankingRenderer 模板渲染
- ✅ 模板变量支持
- ✅ UTF-8 与 Emoji 正常
- ✅ 模板格式错误明确报错（TemplateError，不崩溃）
- ✅ 支持恢复默认模板
- ✅ 每个群可选择 ranking_template（Group 模型字段）
- ✅ 后端模板 CRUD 接口已提供
- ✅ 独立 commit

### Commit
- `(待提交)` P3

---

> 下一轮：P4 DeepSeek V4 Flash 生图 Prompt 流水线
