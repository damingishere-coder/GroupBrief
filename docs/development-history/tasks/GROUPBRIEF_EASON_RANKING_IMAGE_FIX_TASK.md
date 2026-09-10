# GroupBrief Eason 排行榜与图片事实修复任务

## 背景

2026-08-28 的 `Eason张UED-4群🤘`（数据库 `group_id=28`）排行榜使用“所有非系统消息气泡”计数，群友期望改为文字主榜并单列互动数；展示名必须以 WeChatDataAnalysis 返回的 `senderDisplayName` 为准。当前再生图片还包含聊天证据中不存在的 BMI、体脂率和天气数字。

## 目标

1. 为单群提供可配置的排行榜计数与发言人名称策略。
2. 仅为 Eason 群启用“文字主榜＋互动数”和 WeChatDataAnalysis 名称权威策略。
3. 扩展排行榜 JSON/文本并保持旧群、旧模板和下游读取兼容。
4. 为严格模式图片增加基于消息证据和 OCR 的事实校验，事实不明时失败并保持发送锁。
5. 重新读取 Eason 的 2026-08-28 数据、重建榜单和 Prompt、再生合格图片，绝不触发微信发送。

## 允许修改范围

- `app/db`、`app/api/groups.py`、排行榜与模板模块。
- WeChatDataAnalysis Provider/V2 数据源及消息快照兼容字段。
- 每日生成阶段、图片验证和图片再生流程。
- Eason 群配置页面、Dashboard 排行预览及相应类型。
- 与上述行为直接相关的测试、排行榜模板和本任务文件。
- 验证通过后更新本地 Eason 群配置与 2026-08-28 产物。

## 禁止修改范围

- 不改变其他五群的持久化策略或历史产物。
- 不修改 OpenAI/Codex 登录、认证或 Provider 配置。
- 不调用微信发送接口，不清除 `USER_REQUEST_NO_SEND_2026_08_28` 发送锁。
- 不提交 `.env`、日志、缓存、运行时数据库备份、生成图片或其他敏感/大型产物。
- 不 reset、stash、清理、强推或重写 Git 历史。

## 已确定实现要求

- 新增 `ranking_count_policy`，默认 `all_messages`；Eason 使用 `text_primary_with_interactions`。
- 新增 `sender_name_policy`，默认 `resolved`；Eason 使用 `wechat_data_analysis`。
- 文字主榜只按 `message_type=text` 排名，互动为其余可计数非系统类型；同文字数按规范化名称和身份键排序。
- `message_count` 保持所有可计数消息总数；新增文字数、互动数和文字发言人数。`TopSpeaker.count` 在文字主榜中等于文字数，并新增文字/互动/名称来源字段。
- WeChatDataAnalysis 原始展示名必须跨 MCP 与 JSON export 保存；Eason 不得被联系人备注覆盖。身份仍以 `sender_id` 聚合；空名匿名化；同名不同 ID 保持可区分。
- Eason Prompt 追加严格可见文案合同；图片事实校验使用同目录 `messages.json`、Prompt 与 OCR。无证据数字/长文本、OCR 不可用或识别失败不得晋级为可复核图片。
- 严格模式生图事实校验失败时最多生成两次；最终失败保持旧图和发送锁。
- 图片尺寸只校验可解码和正尺寸，不要求固定 1024×1536。

## 验收标准

- 当前 Eason 快照基准：383 条文字、226 条互动、609 条可计数消息、42 名活跃发言人；“深圳-UI-白白”为文字 55、互动 34。
- Eason 的名称来源标记为 `wechat_data_analysis`，不使用联系人备注覆盖。
- OCR/事实测试拒绝 `12%`、`90÷1.72²=30.4`、`120天`、`少油少盐`，允许消息证据中的 `78.8`、`61`、`66`。
- 其他群默认排行榜 JSON/文本和名称解析行为保持兼容。
- Eason 新产物完成后 `send_hold=true` 且 `send_hold_reason=USER_REQUEST_NO_SEND_2026_08_28`，没有新增发送证据。
- SQLite `integrity_check=ok`、外键检查无错误。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v2_ranking.py tests\test_v2_ranking_template.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_wechat_mcp.py tests\test_v2_data_source.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_v2_image_task.py tests\test_v2_image_regeneration.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app tests
Set-Location frontend; npm test; npm run build
```

## 返回格式

- 精确命令、退出码、测试数量和失败证据。
- 修改文件范围、关键行为、Eason 实际产物统计与图片事实复核结果。
- 最终发送锁、Git 提交、分支、远端和推送结果。
