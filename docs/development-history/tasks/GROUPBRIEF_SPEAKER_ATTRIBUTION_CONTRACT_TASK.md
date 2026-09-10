# 群报说话人归属契约修复任务

## 背景

- 2026-08-28 刷新消息和排行榜后，旧选题证据与旧 Prompt 未同步失效，导致同一 `message_id` 在 `messages.json` 和 `prompt_meta` 中出现不同说话人。
- 现有 Poster 编辑模型按姓名选择原话；多人发送相同文本时，仅靠姓名和文本无法证明具体消息归属。
- 图片署名必须使用消息记录当时的有效显示名；无效、系统事件文本或跨身份冲突时才回退已解析名称。统计身份仍以 `sender_id` 为准。

## 目标

- 建立 `message_id + sender_id + 当时显示名 + 原文` 的不可拆分证据绑定。
- 为消息快照和说话人归属生成稳定指纹，并阻止过期选题、Prompt、图片继续进入生图或发送。
- Poster 编辑模型只引用 `message_id`，姓名由程序从当前证据派生。
- 保持公开 HTTP API 和数据库结构不变。

## 允许修改范围

- `app/ai/` 中的消息归属、选题、Poster 编辑和 Prompt 构建。
- `app/pipeline/`、`app/v2/` 中的快照状态、重建和发送闸门。
- 数据源适配器的身份溯源字段保留。
- 对应 Python 测试和本任务文件。

## 禁止修改范围

- 不修改真实 `output/` 历史产物、生产数据库或微信联系人数据库。
- 不触发生图、微信发送、邮件发送、服务重启或历史补跑。
- 不增加图片事后审核或人工审核机制。
- 不修改 OpenAI Provider、认证方式、API Key 或 `.env`。

## 已确定实现要求

- 有效且跨 `sender_id` 唯一的 `upstream_sender_name` 作为该条消息的图片显示名；其余情况回退 `sender_name`，必要时稳定同名编号。
- `evidence_dialogue` 保留 `message_id`、`sender_id`、`speaker`、受控片段和完整原文；完整原文仅供程序校验和内部 provenance 落盘，不提交给编辑模型。
- Poster 编辑 JSON 的人物项使用 `message_id`、`action`、`quote`；程序按消息 ID 派生姓名并校验 quote 只能来自同一条消息。
- selection、`prompt_meta` 和 `run.json` 均写入 `message_snapshot_sha256`、`speaker_fingerprint`；Prompt 元数据和 run 另写 `speaker_bindings`。
- 旧元数据缺少指纹、指纹不匹配、重复消息 ID或证据字段漂移时失败关闭；显式重新选题后才可重建。
- 刷新消息后保留旧文件用于追溯，但标记 `prompt_stale/image_stale`，保持发送锁。

## 验收标准

- 相同原话由不同成员发送时，Poster 必须通过 `message_id` 精确绑定对应身份。
- 同一消息 ID 的姓名、sender_id 或原文改变后，旧选题不能复用。
- 2026-08-28 类型的快照漂移会被阻断；匹配的新快照可正常构建 Prompt。
- 刷新消息不调用总结模型、生图或发送；显式重新选题才允许产生新 Prompt。
- 现有说话人、Prompt、Pipeline、恢复、生图和发送回归测试通过。

## 测试命令

- `.\.venv\Scripts\python.exe -m pytest -q tests/test_contact_resolver.py tests/test_wechat_mcp.py tests/test_sender_name_policy.py tests/test_topic_selection.py tests/test_v2_prompt_builder.py tests/test_v2_pipeline.py`
- `.\.venv\Scripts\python.exe -m pytest -q tests/test_generation_concurrency.py tests/test_reliability_state.py tests/test_v2_image_task.py tests/test_v2_image_regeneration.py tests/test_period_rules.py tests/test_recovery_planner.py tests/test_v2_wechat_native.py tests/test_image_fact_verification.py`
- `.\.venv\Scripts\python.exe -m pytest -q tests`
- `.\.venv\Scripts\python.exe -m compileall -q app tests`
- `git diff --check`

## 返回格式

- 报告根因、字段契约、实际修改、测试结果、未执行的生产动作、提交哈希、分支和推送结果。
