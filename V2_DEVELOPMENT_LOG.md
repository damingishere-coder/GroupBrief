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

## P4 — DeepSeek V4 Flash 生图 Prompt 流水线（2026-08-18）

### 状态：P4 PASS（真实 DeepSeek API 验证通过）

### 做了什么
- `templates/image_prompt/default.md`：生图 Prompt 模板（任务/群名称/统计时间/数据/
  主标题/副标题/整体视觉/版面1~N/底部总结/硬性要求），支持变量
  {{group_name}}/{{period_start}}/{{period_end}}/{{message_count}}/{{speaker_count}}
- `app/ai/prompt_templates.py`：ImagePromptTemplateService（CRUD/恢复默认/校验）+
  DEFAULT_IMAGE_PROMPT_TEMPLATE + render_image_prompt_template
- `app/ai/prompt_builder_types.py`：PromptInput / PromptOutput（含 meta，不含 API Key）
- `app/ai/prompt_builder.py`：DeepSeekImagePromptBuilder
  - 复用 V1 DeepSeekV4FlashProvider 底层 `_chat`（重试/超时），固定 V4 Flash
  - 模板控制输出结构，用户可编辑
  - 超长聊天：分块 → 逐块提取事件(JSON) → 合并去重 → 按模板生成（非暴力截断）
  - 剥离模板 HTML 注释（不进入最终 Prompt）
  - 失败返回 PromptOutput(success=False)，由 pipeline 标记 PROMPT_FAILED
  - meta 记录模板/模式/块数/API 模型/生成时间（不含 API Key）
- 模板 API 增加 image_prompt 端点（/api/v2/templates/image_prompt CRUD）
- 新增单测 `tests/test_v2_prompt_builder.py`（7 项）

### DeepSeek 输入结构
- system：固定硬性约束 + 模板渲染后的【输出结构】
- user：群聊记录（`[HH:MM] 发送者: 内容`，媒体消息打 [图片]/[语音] 等前缀）
- 数据：群名/统计周期/消息数/发言人数（来自代码，禁止 DeepSeek 计算）

### 超长聊天处理方案
`chunk_message_count`（默认 60 行/块）分块 → 每块独立调用 DeepSeek 提取事件 JSON →
合并去重 → 按模板生成完整 Prompt。避免直接截断丢失重要内容。

### 真实验证结果（DeepSeek V4 Flash 真实调用）
- 单块（40 条）：✅ 成功，主标题提到真实事件《牛来》票房破500万
- 分块（415 条 / 7 块）：✅ 成功，输出 2052 字完整结构 Prompt
- 注释剥离：✅ 生成结果直接以【任务】开头
- meta：`{"template":"default","api_model":"deepseek-chat","mode":"chunked","chunk_count":7,...}`

### 测试结果
- pytest 152 passed（新增 7）
- 真实 image_prompt 样例：`output/test-data/image_prompt_full.txt`

### 验收标准
- ✅ 复用现有 V1 DeepSeek 调用能力
- ✅ 固定默认模型 V4 Flash，不纳入 V4 Pro
- ✅ ImagePromptBuilder 已创建
- ✅ templates/image_prompt/ 已创建，保留 V1 结构
- ✅ 模板可编辑（API + 文件）
- ✅ 每个群可选择 image_prompt_template（Group 字段）
- ✅ DeepSeek 输入含聊天/群名/周期/消息数/人数
- ✅ 输出 image_prompt.txt
- ✅ 保存结构化元数据，不含 API Key
- ✅ 失败标记 PROMPT_FAILED（builder 返回失败态）
- ✅ 超长聊天分块/压缩稳定策略
- ✅ 独立 commit

### Commit
- `(待提交)` P4

---

## P5 — Codex `$imagegen` 串行自动生图与落盘（2026-08-18）

### 状态：P5 代码完成，真实调用阻塞（BLOCKED_ON_CODEX）

> ⚠️ 本轮代码实现、单元测试、手动测试入口全部完成；
> 但真实 `$imagegen` 调用因环境缺失 **Codex CLI** 无法验证，
> 按 V2 执行规则第 8 条，**P5 停在真实验收之前**，不得宣布完成。

### 阻塞原因（真实环境核查）
| 依赖 | 现状 |
| --- | --- |
| Codex CLI（`codex` 命令） | ❌ 未安装 / 不在 PATH（`which codex` 无结果；npm 全局无 codex 包） |
| ChatGPT Desktop | ❌ 未安装（`AppData/Local/Programs` 下无） |
| OPENAI_API_KEY | ❌ 未配置（环境变量与 .env 均无；数据库仅有 DeepSeek key） |
| `~/.codex/` 目录 | ✅ 存在（含 auth.json、generated_images 历史图片），但无法调用 CLI |

真实尝试 `scripts/test_image_generation.py health` / `generate` 均返回：
`❌ codex CLI 不可用（未找到命令：codex）`。

### 已完成的代码
- `app/image/image_task.py`：GeneratedImage / ImageTaskResult / detect_image_format /
  verify_image（存在 + 大小>0 + 签名识别）/ copy_generated_image / ImageJob /
  SerialImageQueue（严格串行，单群失败不阻塞其他群，已存在图片跳过，force 重生成）
- `app/image/codex_generator.py`：CodexImageGenerator
  - 调用方式：`codex exec -C . -s workspace-write --skip-git-repo-check "\$imagegen <prompt>"`
  - 落盘策略：调用前快照 `~/.codex/generated_images/`，调用后轮询扫描新图片并复制到目标路径
  - health_check：codex 不可用时返回明确状态
  - 失败绝不把「未落盘」当成功（IMAGE_GENERATION_FAILED / IMAGE_FILE_MISSING）
- `app/config/settings.py` + `.env.example`：codex_path / codex_timeout_seconds /
  codex_generated_images_dir
- `scripts/test_image_generation.py`：health / generate / --test-data 手动入口
- `tests/test_v2_image_task.py`（14 项）：签名识别 / verify_image / 串行顺序 /
  单群失败隔离 / 已存在跳过 / force / codex 不可用判定

### 测试结果
- pytest 166 passed（新增 14），代码链路全部通过单测
- 真实调用：health 与 generate 均正确返回「codex 不可用」，符合预期（阻塞是环境，不是代码）

### 下一步（用户侧）任选其一解除阻塞
1. 安装 Codex CLI（`npm install -g @openai/codex` 或官方安装器），确保 `codex` 在 PATH；
2. 或配置 `OPENAI_API_KEY` 后改用 image_gen 回退脚本；
3. 或提供其他可用的 `$imagegen` 调用途径。

解除阻塞后：`.venv\Scripts\python.exe scripts/test_image_generation.py generate --test-data`
应生成 `output/test-data/test_generated.png` 并通过 verify_image。

### Commit
- `(待提交)` P5（代码完成，标注 BLOCKED）

---

## P6 — 微信发送 Adapter + 自动发送测试（2026-08-18）

### 状态：P6 代码完成，真实发送阻塞（BLOCKED_ON_WECHAT_UIA）

> ⚠️ 代码实现、单元测试、dry-run 全部完成；但真实发送因
> **微信 4.1.12.55 自绘 UI 与 wechat-automation-api（UIA）不兼容**无法验证。
> 按 V2 执行规则第 8 条，P6 停在真实验收之前，不得宣布完成。

### 阻塞原因（真实环境核查 + 实测）
| 检查项 | 结果 |
| --- | --- |
| 微信 PC 客户端 | ✅ Weixin.exe 运行中，版本 **4.1.12.55** |
| 微信窗口 UIA 识别 | ❌ 自绘 UI（`MMUIRenderSubWindowHW`），控件树仅 2 个 Pane |
| wechat-automation-api skill_cli.py | ✅ 可启动（--help 正常） |
| 真实发送（skill_cli sendtext） | ❌ `WECHAT_WINDOW_NOT_FOUND`（依赖 `mmui::MainWindow` 等控件，新版不存在） |
| 键盘导航替代方案 | ❌ 流程执行但消息未送达（文件传输助手最新消息停在 08:28，无测试消息） |
| 截图验证 | ⚠️ 权限系统拦截（真实微信操作需用户授权） |

### 已完成的代码
- `app/sender/wechat_automation.py`：WechatAutomationSender
  - health_check（CLI 存在 + 微信进程 + CLI 探测）
  - send_text / send_image（子进程调用 skill_cli.py，--json 结果解析含 code+message）
  - dry_run 模式（不调用外部）
  - 发送图片前验证路径存在，使用绝对路径
  - 记录发送时间 sent_at
- `app/sender/base.py`：WechatSender 抽象（P0）
- Settings：wechat_automation_cli_path / wechat_automation_python / wechat_window_class
- `scripts/test_wechat_send.py`：health / dry-run / send / send-image（默认目标文件传输助手）
- `tests/test_v2_wechat_sender.py`（8 项）：dry_run 不调用外部 / 图片校验 /
  CLI 不可用 / JSON 解析 / 绝对路径 / 微信进程检测

### 测试结果
- pytest 174 passed（新增 8）
- dry-run：✅ `[dry_run] 发送文字到 文件传输助手` / `[dry_run] 发送图片…`
- health：✅ CLI 可用
- 真实发送：❌ WECHAT_WINDOW_NOT_FOUND（微信版本兼容问题）

### 下一步（用户侧）解除阻塞
1. 微信 4.1.x 新版自绘 UI 不暴露 UIA 控件树，wechat-automation-api 不可用；
   需要：a) 降级/更换可 UIA 识别的微信版本，或 b) 寻找支持自绘 UI 的发送方案
   （如基于窗口截图 + 图像识别的坐标点击，或微信协议库），或 c) 授权调试键盘
   导航方案（需解除权限限制）。
2. 解除后：`.venv\Scripts\python.exe scripts/test_wechat_send.py send --target "文件传输助手" --text "测试"`。

### Commit
- `(待提交)` P6（代码完成，标注 BLOCKED）

---

> 下一轮：P6 真实验证（等待微信发送方案可用）→ P7 全流程 Pipeline + 调度
