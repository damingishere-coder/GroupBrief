# 使用 Docker 开发或只读运行 GroupBrief

Docker 只作为开发、读取和界面验证环境。正式支持环境是 Windows + Alter + 本机微信，由 FastAPI Scheduler 独占调度；Docker 不承诺原生微信发送。微信桌面客户端、WeChatDataAnalysis 和微信 UI 自动化仍运行在 Windows 宿主机上。

## 运行边界

| 组件 | 运行位置 |
| --- | --- |
| FastAPI、Web UI、SQLite、日报流水线、调度器 | Docker 容器 |
| `data/`、`output/`、`logs/` | Windows 宿主机，通过卷挂载持久化 |
| 微信桌面客户端与 WeChatDataAnalysis | Windows 宿主机 |
| Codex CLI 生图 | 默认在宿主机运行；需单独完成本机配置 |
| 微信文字/图片发送 | Windows 宿主机；默认关闭，需完成实机验收 |

## 环境要求

- Windows 10/11
- Docker Desktop，且 Docker 引擎已经启动
- 如需读取真实微信数据：已登录的微信桌面客户端和可用的 WeChatDataAnalysis MCP 服务

## 第一次启动

在仓库根目录打开 PowerShell：

```powershell
Copy-Item .env.example .env
docker compose up -d --build
```

打开 <http://127.0.0.1:8766>。

第一次启动后建议按顺序操作：

1. 打开“设置中心”或“帮助与系统检查”，确认数据库和服务状态。
2. 在“群聊与任务”中搜索并绑定一个群，然后先执行测试读取。
3. 保持微信自动发送关闭，手动生成指定日期的日报。
4. 在排行榜、AI 图片和归档页面复核结果。
5. 只有完成当前微信版本的实机验证后，才考虑开启发送。

## 日常命令

| 操作 | 命令 |
| --- | --- |
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down` |
| 查看状态 | `docker compose ps` |
| 查看日志 | `docker compose logs -f` |
| 代码更新后重新构建 | `docker compose up -d --build` |

## 宿主机 MCP 连接

Compose 会把容器内的 `WECHAT_MCP_URL` 设置为：

```text
http://host.docker.internal:10392/mcp
```

这让容器可以访问 Windows 宿主机上的 WeChatDataAnalysis。真实 Token 只填写在本地 `.env`，不要写入 `docker-compose.yml`、Issue、截图或日志。

## 输出目录

每个群、每个运行日期使用独立目录：

```text
output/
└─ 示例群/
   └─ 2026-01-15/
      ├─ ranking.txt
      ├─ ranking.json
      ├─ messages.json
      ├─ image_prompt.txt
      ├─ daily_image.png
      └─ run.json
```

其中 `messages.json`、Prompt 和图片可能包含聊天内容。`output/` 默认不进入 Git，请不要手动提交。

## 修改访问端口

只修改宿主机端口映射即可。例如把：

```yaml
ports:
  - "8766:8766"
```

改为：

```yaml
ports:
  - "8767:8766"
```

重新执行 `docker compose up -d` 后访问 <http://127.0.0.1:8767>。

## 常见问题

### 页面无法打开

```powershell
docker compose ps
docker compose logs --tail 100
```

确认容器状态为 running，并检查 8766 端口是否被占用。

### WeChatDataAnalysis 不可用

- 确认 Windows 上的 WeChatDataAnalysis 已启动。
- 确认 MCP 地址和 Token 与本机服务一致。
- 不要把容器内地址改回 `127.0.0.1`；容器访问宿主机应使用 `host.docker.internal`。

### Codex 生图不可用

容器不会自动获得宿主机 Codex 的登录状态。请先使用网页中的健康检查确认生图路径；必要时参考 [`CODEX_IMAGE_AUTOMATION_PROMPT.md`](CODEX_IMAGE_AUTOMATION_PROMPT.md) 进行人工兜底。

### 微信发送不可用

微信发送依赖 Windows 桌面、已登录微信、未锁屏状态和当前客户端兼容性。该能力默认关闭，自动化测试通过不代表真实微信环境已经验收。

## 隐私说明

- 聊天数据、数据库、日报和日志保存在本机挂载目录。
- 使用 Codex 或 DeepSeek 时，完成总结/生图所需的内容会发送到用户选择的模型服务。
- `.env` 不进入镜像，也被 Git 忽略；仍应避免把它复制到公共位置。
