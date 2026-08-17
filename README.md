# 群报 GroupBrief

微信群聊日报小工具 · Windows 本地版 · V1

GroupBrief 从 Windows PC 微信的本地聊天记录中读取多个群聊，精确统计每日发言排行榜，并由 DeepSeek 分析当天真实聊天事件生成「可直接交给 GPT 生图」的海报 Prompt，最终每天 09:00 通过一封邮件发送给你。

```
Windows PC 微信
    ↓
WeChatDataAnalysis（主读取）→ 失败时 wechat-cli（备用）
    ↓
GroupBrief 本地整理
    ↓
精确排行榜（程序确定性计算，LLM 不参与数字统计）
    +
DeepSeek V4 Flash 分析聊天 → GPT 生图 Prompt
    ↓
按 日期/群 保存本地文件（output/）
    ↓
每天 09:00 发送一封纯文字邮件
    ↓
你手动：复制排行榜 / 复制 Prompt → GPT 生图 → 发微信群
```

## V1 功能

- 多群管理：添加 / 删除 / 启用 / 停用任意数量的群（首批两个群）
- 精确排行榜：总消息数、发言人数、Top10，格式固定
- DeepSeek V4 Flash 生成海报 Prompt（未配置 API Key 时使用本地模板，全链路可用）
- 自动调度：周一～周六 08:45 生成、09:00 发邮件；周日不执行；周一统计周六+周日两天
- 手动执行：指定日期、指定群、重新生成（force）、手动发邮件
- 本地文件归档：`output/YYYY-MM-DD/{群}/`（含 V2 Handoff 交接文件）
- 本地 Web UI（Apple 蓝白风格）：仪表盘 / 群聊管理 / 执行记录 / 文件管理 / 日志 / 设置 / 关于
- 隐私：聊天记录只在本机读取，只有生成 Prompt 所需文本提交给 DeepSeek API

## V2 规划（V1 只预留接口）

V2 由 Codex Automation 接管：读取 `image_prompt.txt` → GPT 自动生图 → 自动打开微信 → 发送排行榜文字 + 海报图片。
V1 已预留：`ImageGenerationProvider`、`WeChatDeliveryProvider`、`handoff.json`（poster_file / poster_status）、UI 海报预览占位。

## 安装

环境要求：Windows 10/11、Python 3.10+、Node.js 18+（仅构建前端时需要）。

```bat
cd /d "C:\Users\10578\Documents\AI - GroupBrief"
start_windows.bat
```

首次运行会自动：创建 Python 虚拟环境 → 安装依赖 → 构建前端 → 启动服务。
之后每次直接运行 `start_windows.bat` 即可。

## 启动

```bat
start_windows.bat
```

浏览器打开：<http://127.0.0.1:8766>

> 注：文档默认端口 8765 被本机其他项目占用，本项目使用 8766。如需修改，编辑 `.env` 中的 `APP_PORT`。

## 配置

复制 `.env.example` 为 `.env` 并填写：

| 配置 | 说明 |
| --- | --- |
| `AI_API_KEY` | DeepSeek API Key（配置后启用真实 AI；未配置自动使用本地模板） |
| `AI_BASE_URL` / `AI_MODEL` | DeepSeek 接口地址与模型（默认 `https://api.deepseek.com` / `deepseek-chat`） |
| `EMAIL_ENABLED=true` | 启用邮件 |
| `EMAIL_RECIPIENT` | 收件地址 |
| `EMAIL_SMTP_HOST/PORT/USER/PASSWORD` | SMTP 配置（默认 465 SSL） |
| `WECHAT_DATA_DIR` | 微信数据目录（WeChatDataAnalysis 用） |
| `WECHAT_CLI_PATH` | wechat-cli 可执行文件路径 |

也可以在网页「配置设置」页修改（API Key 只显示掩码）。

## 微信 Provider

| Provider | 角色 | 说明 |
| --- | --- | --- |
| WeChatDataAnalysis | 主 | 读取 `data/wechat_export/` 下导出的 JSON（groups.json + messages/{群}/{日期}.json，格式与统一消息模型一致） |
| wechat-cli | 备用 | 调用 `wechat-cli export/list-groups` 命令 |
| Mock | 兜底 | 读取 `fixtures/` 模拟数据，无真实微信时保证全链路可开发可测试 |

降级链：主 Provider 不可用 → 备用 → Mock。所有 Provider 通过统一 `ChatHistoryProvider` 接口接入，业务层不依赖任何开源项目内部实现。Provider 状态在仪表盘「系统状态」区可见。

> 真实微信读取为 REAL_ENV_PENDING：需在装有微信并登录的机器上，使用 WeChatDataAnalysis 导出数据（或配置 wechat-cli 路径）后自动生效。

## DeepSeek

- 只负责：聊天内容 → 理解事件 → 整理话题 → 生成 GPT 生图 Prompt（输出 `image_prompt.txt`）
- 不负责：排行榜计算、微信读取、邮件、调度、文件管理
- 超长群聊自动按 `CHUNK_MESSAGE_COUNT` 分块分析后合并
- Prompt 硬性约束：不编造事件 / 人物 / 金额 / 时间 / 地点，原话必须来自真实聊天

## 邮件

- 每天只发送一封，包含所有启用群，每群 = 排行榜 + GPT 生图 Prompt
- 发送前检查群报告是否成功生成，不发送空白结果
- `EMAIL_SEND_PARTIAL_REPORT=true`（默认）：部分群失败仍发送成功群

## 自动任务

- 08:45 `GenerateDailyReports`：读取 → 整理 → 排行 → Prompt → 保存文件
- 09:00 `SendDailyEmail`
- 周日不执行
- 周一统计周六 00:00:00 ～ 周日 23:59:59（两天汇总）；周二～周六统计前一天
- 防重复：同一 日期+群+时间范围 已成功不重复生成；手动「重新生成」= force

## 文件输出

```
output/
└─ YYYY-MM-DD/
   ├─ {群}/
   │  ├─ ranking.txt
   │  ├─ image_prompt.txt
   │  ├─ meta.json
   │  ├─ normalized_messages.json
   │  └─ handoff.json
```

`handoff.json` 是 V1→V2 的机器可读交接协议（version/date/group/ranking_file/prompt_file/poster_file/status）。

## 隐私

- 微信聊天只在本机读取，不自动上传整个聊天库
- 仅生成 Prompt 所需的整理文本提交给 DeepSeek API
- `output/`、`data/`、`logs/` 均不入 Git
- 日志不记录 API Key / 邮件密码

## 常见故障

| 现象 | 处理 |
| --- | --- |
| 仪表盘 Provider 显示不可用 | 未配置真实微信读取，属正常；会使用 Mock 数据，或按提示配置 WeChatDataAnalysis 导出 / wechat-cli |
| Prompt 显示模板内容 | 未配置 `AI_API_KEY`，本地模板保证可用；配置 Key 后自动使用 DeepSeek |
| 邮件发送失败 | 检查 SMTP 主机/端口/账号/授权码，网页「配置设置」或 `.env` |
| 端口被占用 | 修改 `.env` 的 `APP_PORT` 后重启 |
| 周日没有自动任务 | 正常，周日不执行 |
| 生成报错 | 查看「日志」页，或 `logs/` 下对应分类日志 |

## 开发

```bat
cd frontend
npm install
npm run dev        :: 开发模式（5173，代理 /api 到 8766）
```

后端测试：

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
```

## 目录结构

```
app/            FastAPI 后端（api / providers / services / scheduler / db / config）
frontend/       React + Vite + TypeScript（Apple 风格 UI）
fixtures/       模拟聊天数据（Mock Provider）
scripts/        工具脚本（fixtures 生成器等）
output/         每日群报输出（不入 Git）
data/           SQLite 与导出缓存（不入 Git）
logs/           分类日志（不入 Git）
tests/          pytest 自动化测试
```
