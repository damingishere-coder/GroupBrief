# Security Policy

GroupBrief 会处理本地聊天记录、模型凭据、SMTP 配置和桌面自动化。报告安全问题时，请优先避免二次泄露。

## 支持版本

| 版本 | 安全更新 |
| --- | --- |
| 1.x | 支持 |
| 早期开发快照 | 不支持 |

## 私密报告漏洞

请优先使用 GitHub 仓库 Security 页面中的 **Private vulnerability reporting** 提交安全报告：

<https://github.com/damingishere-coder/GroupBrief/security/advisories/new>

如果该入口暂时不可用，请创建一个不包含技术细节和敏感数据的公开 Issue，请维护者开启私密沟通渠道。不要在公开 Issue 中粘贴：

- API Key、Token、Cookie、SMTP 密码或 `.env` 内容
- 真实群名、群 ID、微信号、成员昵称或聊天正文
- 数据库、完整日志、运行目录、截图或本机绝对路径
- 可直接利用的漏洞细节

报告中可以安全提供：受影响版本、问题类别、最小化复现步骤、预期影响，以及已经完成的脱敏说明。

## 使用者安全边界

- 只处理你有权访问和使用的聊天数据。
- `.env`、`data/`、`output/` 和 `logs/` 应始终保留在本机并排除出 Git。
- 微信发送默认关闭；开启前先使用无副作用目标完成实机验证。
- 使用 Codex、DeepSeek 或 SMTP 时，数据会进入你选择的外部服务，请遵守相应服务条款和数据政策。
