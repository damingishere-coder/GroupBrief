# Changelog

GroupBrief 使用 [Semantic Versioning](https://semver.org/) 记录公开版本。

## [1.0.0] - 2026-08-23

首个公开稳定版本。

### Available now

- Windows 本地 Web 工作台，提供群聊与任务、排行榜、AI 图片、聊天归档和设置页面。
- 通过 WeChatDataAnalysis MCP 或结构化 JSON 读取群聊历史，并支持群发现、绑定和测试读取。
- 使用程序确定性计算消息数、发言人数和 Top10，LLM 不参与数字统计。
- 使用 Codex GPT 生成群聊摘要和海报 Prompt，可选 DeepSeek 备用。
- 按群和日期保存排行榜、消息归档、Prompt、图片和运行状态。
- 提供手动执行、每日调度、失败隔离、启动补偿和邮件发送能力。
- 提供图片主题、Prompt 编辑、重新生图和发送前复核流程。
- 微信文字与图片发送适配器默认关闭，并保留发送状态与防重复保护。

### Current limitations

- 真实微信读取依赖用户自己的 Windows 微信环境和 WeChatDataAnalysis 服务。
- Codex 生图依赖本机 Codex CLI、登录状态和 ImageGen 能力。
- 邮件依赖用户提供的 SMTP 配置。
- 微信原生发送已通过自动化测试，但尚未形成覆盖不同微信版本和桌面环境的可重复实机验收；请保持关闭，直到自行完成测试。
- 自动化测试通过不代表上述外部服务已经在所有机器完成端到端验证。

[1.0.0]: https://github.com/damingishere-coder/GroupBrief/releases/tag/v1.0.0
