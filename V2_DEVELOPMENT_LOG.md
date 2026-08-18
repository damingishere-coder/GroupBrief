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

> 下一轮：P2 统计周期引擎 + 排行榜统计
