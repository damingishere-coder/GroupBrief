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

## P7 — 全流程 Pipeline + 调度（2026-08-18）

### 状态：P7 代码 PASS；真实发送/生图验收受 P5/P6 阻塞

### 做了什么
- `app/v2/run_store.py`：RunStore —— output/<群>/<日期>/run.json 状态存储
  （PENDING→DATA_READY→RANKING_READY→PROMPT_READY→IMAGE_READY→READY_TO_SEND→SENT/FAILED）
- `app/pipeline/daily_pipeline.py`：DailyPipeline
  - 生成阶段：取数(messages.json)→排行(ranking.json/txt)→DeepSeek(image_prompt.txt)
    →生图(daily_image.png)→READY_TO_SEND
  - 发送阶段：到点群 send_text(ranking.txt)→send_image(daily_image.png)→SENT
  - 每群独立状态；某群失败不阻塞其他群；生图全局单队列串行
  - 防重复：同群同周期已到终态跳过；SENT 绝不重复发送
  - 周六周日跳过；周一统计周五~周日；force_generate / force_send
  - 失败标记 FAILED + failed_stage + 错误类型
- `scripts/run_daily_pipeline.py`：统一入口
  （generate/send/force-generate/force-send/status）
- `tests/test_v2_pipeline.py`（14 项集成测试，注入 Fake 依赖隔离外部）

### 状态流（真实 dry-run 茶馆群验证）
```
PENDING → DATA_READY(409条) → RANKING_READY(27人/409) → PROMPT_READY(DeepSeek 7块)
→ 生图阶段：codex 不可用 → FAILED(IMAGE_GENERATION_FAILED)   # 诚实失败，不假装成功
```
输出文件：messages.json / ranking.json / ranking.txt / image_prompt.txt / run.json 全部生成；
run.json 记录错误类型与 image_error。

### 各阶段耗时（真实茶馆群）
- MCP 取数 + 排行 + DeepSeek 7 块分块生成：约 48 秒（19:31:27 完成 prompt，
  run.json 19:32:15 落盘）

### 测试结果
- pytest 188 passed（新增 14）；全量无回归

### 验收标准
- ✅ 完整状态流（PENDING→…→FAILED 全程可追踪）
- ✅ 一次 dry_run 全流程（真实数据到 PROMPT_READY，生图阶段诚实失败）
- ⚠️ 一次真实测试群全流程：生图/发送受 P5/P6 阻塞（codex 未安装、微信 UIA 不兼容）
- ✅ 各阶段独立状态、失败隔离、全局串行、防重复、force、周六日跳过、周一三天
- ✅ 独立 commit

### Commit
- `(待提交)` P7

---

## P8 — V2 前端重构（2026-08-18）

### 状态：P8 PASS（后端 API + 前端构建 + 端到端验证通过）

### 做了什么（前端）
- 全新导航：今日概览 / 群管理 / 模板中心 / 历史日报 / 系统状态（移除 V1 的执行记录/
  文件管理/实时监听等，V2 不展示实时监听）
- `pages/v2/Dashboard.tsx`：今日日期、统计周期、启用群数、待生成/已生成/已发送/失败
  统计卡、下次发送时间、每群卡片（群名/状态徽标/周期/发送时间/消息数/发言数/图片
  缩略图/错误提示/立即生成/立即发送/危险操作确认）
- `pages/v2/Groups.tsx`：群管理——新增/编辑/停用/删除，V2 全字段配置
  （发送时间/周期规则/发送目标/是否生图/排行模板/Prompt 模板/Prompt 模型/启用），
  删除需确认
- `pages/v2/Templates.tsx`：模板中心——排行榜模板 + 生图 Prompt 模板，在线编辑/
  保存/预览（排行榜支持示例数据渲染预览）/恢复默认/删除（默认不可删）
- `pages/v2/History.tsx`：历史日报——按 群→日期 列表，详情展示状态/周期/图片/
  排行榜文本/Prompt 文本
- `pages/v2/System.tsx`：系统状态——WeChatDataAnalysis/DeepSeek/Codex/微信发送/
  输出目录/模板资产健康检查 + 运行环境提示
- Apple 蓝白风格：复用现有设计语言 + 追加 V2 组件样式（群卡片/模板编辑器/历史/系统）
- 删除未引用的 V1 旧页面文件

### 做了什么（后端 API 支撑）
- `app/api/v2_ui.py`：GET /api/v2/dashboard、/runs、/runs/{group}/{date}、
  /system/health、POST /pipeline/generate、/send-due、/send、GET /files/...
- `app/api/groups.py` 扩展 V2 字段（GroupCreate/GroupUpdate/列表返回）
- `app/main.py` 注册 v2_ui router
- 修复 RunStore.list_runs（run_date 缺省时遍历日期子目录）

### 验证结果
- 前端 `npm run build` 通过（tsc 严格 + vite）
- 后端 pytest 188 passed 无回归
- 端到端（真实环境启动 uvicorn）：首页 200、dashboard 返回真实状态
  （茶馆 08-18 FAILED / Grok pending）、system/health 真实检测
  （wechat_data OK / deepseek OK / codex UNAVAILABLE / wechat_sender UNAVAILABLE）
- 端口 8766 占用清理：终止了残留的旧 V1 服务进程

### 验收标准
- ✅ Apple 蓝白风格、大面积留白、简洁大气
- ✅ 不展示实时监听/消息流/机器人面板
- ✅ 五大核心页面齐全（Dashboard/群管理/模板中心/历史日报/系统状态）
- ✅ 每群卡片含状态/缩略图/立即生成/立即发送
- ✅ 群配置含全部 V2 字段，支持新增/停用
- ✅ 模板中心在线编辑/保存/预览/恢复默认/绑定
- ✅ 历史日报按 群→日期 展示完整内容
- ✅ 系统状态显示外部依赖健康 + 环境提示
- ✅ 关键操作连接真实后端 API
- ✅ 危险操作（删除/发送）有确认
- ✅ 独立 commit

### Commit
- `(待提交)` P8

---

## P9 — Windows 无人值守稳定性（2026-08-18）

### 状态：P9 PASS（代码 + 测试 + 真实环境冒烟通过）

### 做了什么
1. **启动检查** `app/core/startup_check.py`：WeChatDataAnalysis 数据源 / 微信进程 /
   DeepSeek 配置 / output 可写 / templates 完整。任一失败仅记录日志不阻止启动
   （避免单点失败导致服务退出）。main.py lifespan 启动时执行。
2. **开机自启** `scripts/install_autostart.py`：注册表 Run 键（HKCU）注册/卸载/查询，
   仅在用户登录后运行（不绕过锁屏安全机制）。
3. **任务调度自动恢复 / 异常退出恢复** `app/v2/recovery.py`：
   - scan_incomplete：找出未终态 run，按 recovery_type 区分「send」（生成已齐备
     触发发送）与「generate」（生成中断重跑）；
   - verify_output：输出文件完整性检查（messages/ranking/prompt/image 缺失检测）；
   - recover_incomplete + API retry-failed：手动/启动时重跑。
4. **防重复**：SENT 绝不重发（pipeline.send_due 检查 sent_at）；IMAGE_READY 跳过
   重复生图（pipeline 防重复逻辑，P7 已实现并验证）。
5. **日志轮转**：RotatingFileHandler（5MB×5）+ `clean_old_logs` 按 `LOG_RETENTION_DAYS`
   （30 天）清理过期日志，启动时执行。
6. **健康页与提示**：/api/v2/system/health 增加 recent_task（最近任务）、warnings
   （休眠/锁屏风险提示）；/api/v2/system/startup（启动检查）、/system/recovery；
   前端 System 页展示启动检查/恢复信息 + 「重跑未完成任务」按钮（危险操作确认）。

### 真实验证（启动服务冒烟）
- startup：WeChatDataAnalysis OK / 微信 OK / DeepSeek OK / 输出 OK / 模板 OK
- recovery：incomplete 0、integrity 1（茶馆 FAILED 为终态，正确排除）
- health：warnings 1（锁屏/休眠提示）、recent_task 显示最近茶馆 FAILED
- 开机自启：status 查询正常（未安装）

### 测试结果
- pytest 193 passed（新增 5）
- 前端构建通过（System 页含恢复/重跑）

### 验收标准
- ✅ 启动检查五项
- ✅ 开机自启支持（脚本 install/uninstall/status）
- ✅ 调度自动恢复（未完成任务分类恢复）
- ✅ 异常退出后恢复未完成
- ✅ SENT 绝不重复发送
- ✅ IMAGE_READY 跳过重复生图
- ✅ 日志轮转 + 最大保留天数（30 天）
- ✅ output 文件完整性检查
- ✅ 单群失败不导致服务退出
- ✅ 运行健康页 + 最近任务
- ✅ 手动重跑失败任务
- ✅ 休眠/锁屏风险提示
- ✅ 不绕过系统锁屏安全机制
- ✅ 独立 commit

### Commit
- `(待提交)` P9

---

## P0~P9 汇总状态

| 轮次 | 状态 | 说明 |
| --- | --- | --- |
| P0 基线固化 | ✅ PASS | commit 5b94840 |
| P1 数据接入 | ✅ PASS | commit cf48d50，真实 MCP 取数验证 |
| P2 周期+排行 | ✅ PASS | commit f2bbc3f，真实统计与文档一致 |
| P3 排行模板 | ✅ PASS | commit 1639aa3 |
| P4 DeepSeek Prompt | ✅ PASS | commit 2b189c0，真实 API 验证 |
| P5 Codex 生图 | ⚠️ BLOCKED | commit e2a5cd7，代码完成待 Codex CLI |
| P6 微信发送 | ⚠️ BLOCKED | commit 0751987，代码完成待微信 UIA 方案 |
| P7 Pipeline | ✅ PASS(代码) | commit 13b33c6，真实 dry-run 到生图阶段 |
| P8 前端重构 | ✅ PASS | commit 06ef795，构建+端到端通过 |
| P9 稳定性 | ✅ PASS | 本轮 commit |

**真实外部阻塞（待用户处理）**：
1. P5：Codex CLI 未安装 → `npm install -g @openai/codex` 后跑
   `scripts/test_image_generation.py generate --test-data`；
2. P6：微信 4.1.12 自绘 UI 与 wechat-automation-api（UIA）不兼容 →
   需降级/更换可 UIA 识别的微信版本或授权替代方案。

---

## Docker 化（2026-08-18）

### 状态：PASS（构建 + 启动 + 真实归档验证通过）

### 做了什么
- `Dockerfile` 多阶段构建：node:20-alpine 构建前端 → python:3.12-slim 运行后端
- `docker-compose.yml`：端口 8766、数据卷（./data ./output ./logs）、
  env_file .env（token/Key）+ 环境变量（WECHAT_MCP_URL=host.docker.internal 等）
- `.dockerignore`：排除 .venv/node_modules/data/output/logs/.env 等
- `docs/DOCKER.md`：保姆级启动/使用/常见问题文档
- 代码改动：
  - settings 新增 `wechat_mcp_allowed_hosts`（额外允许的 MCP 主机）
  - wechat_mcp.py：MCPClient / build_mcp_client 支持 allowed_hosts（默认仍仅回环）
  - wechat_data_analysis.py：透传 allowed_hosts + `_parse_allowed_hosts`
  - repository.apply_db_settings：环境相关字段（WECHAT_MCP_URL / ALLOWED_HOSTS）
    在环境变量显式设置时优先于数据库（容器与宿主机直跑共用数据库的关键）
- `tests/test_mcp_allowed_hosts.py`（7 项）

### 验证结果（真实环境）
- ✅ 镜像构建成功（groupbrief-v2:latest）
- ✅ 容器启动，前端 http://127.0.0.1:8766 返回 200
- ✅ 容器通过 host.docker.internal 访问宿主机 WeChatDataAnalysis MCP
  （system/health wechat_data_analysis=OK、deepseek=OK）
- ✅ 容器内真实生成：文案按「群名/日期」归档到宿主机 output 卷
  （茶馆V3.0（三周年纪念）🐮🐴/2026-08-18/ 含 image_prompt.txt 等）
- ✅ image_prompt.txt 为真实 DeepSeek 生成（409 条 / 7 块 chunked）
- ✅ pytest 200 passed（新增 7）

### 架构说明
容器只跑服务本体；微信发送（UI 自动化）与 WeChatDataAnalysis 桌面服务
留在 Windows 宿主机（UIA 依赖桌面会话），容器经 host.docker.internal 访问。

### Commit
- `bcc0233` Docker 化

---

## 微信发送方案验证（2026-08-18，P6 结论更新）

### 状态：P6 确认暂缓（用户决定"自动发送先不做"）

### 做了什么
1. **调研 GitHub 开源项目**（应用户要求）：
   - LAVARONG/wechat-automation-api（Flask + uiautomation，支持微信 4.0+）
   - cluic/wxauto、Hello-Mr-Crab/pyweixin、xieyumc/YuYuWechat、hanggezhuai/qclaw_Weixin
   - 关键技巧发现：微信 4.x 默认隐藏 UIA 元素，需先开启 Windows「讲述人」再登录微信
2. **验证讲述人技巧**（用户手动激活 + 重启微信）：
   - 讲述人已运行、微信已重启，但微信窗口 UIA 控件树**仍只有 2 层空白 Pane**
     （Qt51514QWindowIcon → MMUIRenderSubWindowHW）
   - 输入框 `mmui::ChatInputField`、会话列表、搜索框均无法定位
   - 结论：**讲述人技巧在微信 4.1.12.55 上无效**，所有依赖 UIA 控件树的开源项目均不可用
3. **用户决定**：微信自动发送先不做（P6 保持暂缓）

### 系统现状（半自动模式）
- ✅ 自动生成排行榜（ranking.txt）+ 生图文案（image_prompt.txt）→ 按 群名/日期 归档
- ✅ 前端"今日概览"发送按钮在微信发送未启用时禁用并提示（避免误操作）
- ⚠️ 发送环节：用户手动复制文案到微信（或未来降级微信版本/换方案后再接）

### 后续解锁方式（备忘）
1. 降级微信到 3.9.x / 4.0 早期（UIA 控件树开放）→ wechat-automation-api 可用；
2. 或接受截图+OCR 坐标方案（需配合测量窗口，较脆弱）。
