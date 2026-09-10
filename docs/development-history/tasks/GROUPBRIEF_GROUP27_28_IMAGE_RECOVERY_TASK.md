# 米游3.2 与 Eason 群报生图恢复任务

## 背景

2026-08-28：

- 米游涩泛二次元同好摸鱼群3.2 的 ImageGen 调用因连接失败没有生成候选图。旧结构化回执只能填写 `image_path`，错误说明被写进该字段，系统只能按“结果未知”失败关闭。
- Eason张UED-4群🤘 的漫画编辑稿引用了一条本身以省略号结尾的完整真实原话，被通用“悬空省略号”规则误判；三次编辑重试后进入本地简化信息图兜底，并已于 08:30 发送。
- Prompt 失败降级时只保存了最小兜底元数据，丢失已计算的话题选择与分镜信息，后续不能使用常规安全重建路径。

用户要求查明并修复错误，为两个群重新生图，但今天不得再次发送。

## 目标

- 区分 ImageGen 的明确失败与真正的结果未知，避免错误说明伪装成图片路径。
- 允许逐字引用本身以省略号结尾的完整原消息，但继续拒绝人为截断出来的悬空省略号。
- Prompt 失败时保留已经形成的非敏感话题与布局元数据，便于之后安全重建。
- 为缺少历史话题快照的旧兜底运行提供显式授权的“仅从已保存消息重新选题并重建 Prompt”恢复入口。
- 按顺序为米游3.2、Eason 重新生图，保持两群今日发送锁，不调用任何发送接口。

## 允许修改范围

- `app/ai/poster_copy.py`
- `app/ai/prompt_builder.py`
- `app/pipeline/generation_stages.py`
- `app/pipeline/daily_pipeline.py`
- `app/v2/run_store.py`
- `app/image/codex_generator.py`
- `app/image/regeneration.py`
- `app/image/codex_image_result.schema.json`
- 与上述逻辑直接相关的测试。
- 两群 2026-08-28 的本地 Prompt、生图任务状态和正式图片产物。

## 禁止修改范围

- 微信发送器、群目标身份、发送记录和已发送历史。
- 当天 messages.json、聊天事实和候选图片归属/哈希/路径边界规则。
- 调度时间、群配置、数据库结构、Secrets 和远端部署。
- 自动重试结果未知的旧任务、猜测候选图或并行启动两个 ImageGen。
- 解除 `USER_REQUEST_NO_SEND_2026_08_28` 发送锁。

## 已确定实现要求

1. ImageGen 结构化回执必须包含 `status`、`image_path`、`error`；成功只接受绝对图片路径，失败只接受空路径和非空错误。
2. 明确失败返回 `outcome_unknown=false`，不得被登记为候选或自动猜图；同一任务仍只调用一次 ImageGen。
3. 完整原消息逐字以省略号结尾时可作为真实气泡；任何仅截取原消息一部分并以省略号结尾的气泡仍拒绝。
4. PromptBuilder 的已形成元数据随普通失败结果返回；本地兜底元数据在其基础上追加，不覆盖话题选择。
5. 新 Prompt 成功落盘时必须清除旧的本地图片兜底标记；缺少旧话题快照时，只有显式 `allow_topic_reselection=true` 才能从保存的 messages.json 重新选题，不得重新读取微信。
6. Eason 重建 Prompt 后再生图；米游3.2直接使用已验证的现有 Prompt 生图；两项严格串行。
7. 生图完成后两群保持 `send_hold=true`、`needs_manual_send=true`，且本次不新增 `sent_at`、`text_sent_at` 或 `image_sent_at`。
8. 重生图成功后必须清除旧的本地兜底标记，并记录本次尝试数量，避免面板把真实 ImageGen 新图继续显示成 Pillow 兜底图。

## 验收标准

- 新失败回执测试证明网络/工具明确失败不会再成为“结果未知”，旧版成功回执仍可兼容读取。
- 省略号测试同时覆盖“完整原消息允许”和“截断片段拒绝”。
- Prompt 失败兜底测试证明 `topic_selection` 等元数据仍保留。
- 显式话题重选测试证明只复用保存消息、不取微信、不生图，并保留 SENT 历史和发送锁。
- 重生图成功测试证明旧 `fallback_level`、`fallback_reason`、`image_variant` 和强制本地兜底开关均被清除。
- 两张新图片可完整解码，正式文件与任务回执的 job_id、Prompt 哈希和 SHA-256 一致。
- Eason 原 08:30 发送历史不改变；米游3.2仍无发送历史；两个群本次均无发送调用。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_v2_prompt_builder.py tests/test_v2_pipeline.py tests/test_v2_image_task.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_v2_image_regeneration.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app tests
```

## 返回格式

- 两群已证实根因。
- 代码修复与运行恢复结果。
- 两张图片路径、尺寸、哈希、任务回执和发送锁证据。
- 测试、Git 提交、分支与推送结果。
- 已发送事实和未执行事项。
