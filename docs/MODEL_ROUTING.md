# 群报模型分工

群设置分别保存两项能力：

| 能力 | Provider | 模型 |
| --- | --- | --- |
| 聊天分析、事件卡、候选话题 | `summary_provider=deepseek` | `summary_model=deepseek-flash` |
| 分镜、漫画文案、周榜祝贺 | `prompt_provider=codex` | `prompt_model=gpt-5.6-luna` |

Codex 文字与生图执行共用 `CODEX_REASONING_EFFORT=max`。生图子进程显式使用
`CODEX_IMAGE_MODEL=gpt-5.6-luna` 调用 ImageGen；此配置不改变 ImageGen 本身的图片模型，
也不修改用户的 Codex 全局设置和登录态。建议 `SUMMARY_PROVIDER_FALLBACK=none`，
避免业务模型失败时自动切换模型。DeepSeek 请求明确关闭思考模式。

已有群需要同时更新 Provider 与模型字段，不能只改全局默认值；新群继承现有启用群
最常见的整套配置。停用群更改模型不会启用该群。旧显式模型仍可读取，历史记录不改写。

`prompt_meta.summary_usage` / `prompt_usage` 保存服务端返回的用量；生图 attempt
保存执行模型、思考强度及 `turn.completed` 用量。缺失用量不视为零；Codex token
不能直接换算订阅额度百分比，生图工具也可能另有消耗。每个并发群独享 Provider 状态。

## 独立日报对比

在项目根目录运行 `python -m scripts.compare_model_snapshot --source-dir <原日报目录>
--output-dir <全新独立目录>`。密钥由本地 `.env` 的 `AI_API_KEY` 提供，禁止放入命令参数。

脚本固定采用 DeepSeek 分析、Luna Max 文案与生图执行；复用聊天快照，但不复用旧选题、
分镜和提示词。记录原文件 SHA-256，将原图复制为 `original.png`，新图为
`daily_image.png`。只提交一次图片生成，不入发送队列；目标目录已存在时拒绝再次执行。
请求结果未知时必须检查已有证据，不能通过换目录绕过保护重新收费调用。
