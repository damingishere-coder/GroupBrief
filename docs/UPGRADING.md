# 从 1.0 升级到 2.0

2.0 统一了新版创作工作台和产品版本。本次版本整理相对发布前主线不新增数据库迁移，也不更改 `/api/v2` 路由或用户既有群配置。早期 1.0 安装与持续更新的 1.x 安装可能处于不同数据库结构，不能仅凭界面版本判断能否直接升级。

## 升级前

1. 等待生成、图片处理和发送任务结束；结果未知的任务先核对，避免重启后误判。
2. 查看 `git status`。有未提交修改时先保留到独立分支或另行备份；不要用 `reset --hard`、强制切换或覆盖解决同步问题。
3. 记录当前提交与服务管理方式。停止已有服务后，备份 `.env`、`data/`（包含 SQLite 及其辅助文件）、`output/`、自定义模板和 `frontend/dist/`。备份放在仓库外，不能提交到 GitHub。

## 早期数据库的额外检查

如果启动提示 `DatabaseSchemaError`、缺少 `schema_migrations` 或要求 P0.2B 离线迁移，保持服务停止，不要删除数据库或绕过校验。先在副本上按现有迁移工具检查：

```powershell
.\.venv\Scripts\python.exe scripts/migrate_db.py --source data/groupbrief.db --output data/groupbrief-v2-copy.db --dry-run
```

上例适用于默认数据库路径，输出路径必须不存在。`--dry-run` 只检查，不替换原库；自定义安装使用实际路径。真正迁移需要另外执行 `--apply` 生成副本、检查 manifest 与完整性，并在保留原库后安排切换。出现错误先核对工具报告，不能将干净新安装的测试当作旧库迁移验收。

## 更新已有安装

在项目目录打开 PowerShell。仅当工作区干净、当前在 `master` 且没有本地独有提交时运行：

```powershell
git fetch origin --prune
git switch master
git pull --ff-only origin master
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c requirements.lock
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

如提示无法快进，停止并核对分支差异，不要强推或覆盖。`master` 是持续更新线；需要精确复现 2.0.0 时，在独立干净目录检出 `v2.0.0` 标签。

通过原来的 RunDock / Alter 项目重新启动服务；手动启动的安装则重新运行 `.\start_windows.bat`。不要让托管服务和手动脚本同时占用 8766。启动脚本会复用已有前端，所以升级时必须显式执行上面的构建。

## 验收

```powershell
Invoke-RestMethod http://127.0.0.1:8766/api/version
Invoke-RestMethod http://127.0.0.1:8766/api/system/ready
```

精确安装 2.0.0 时，版本应为 `2.0.0`，就绪结果应为 `ready: true`。浏览器按 `Ctrl+F5`，确认左侧有“今日工作台、日报作品、群聊管理、运行任务、消息归档、设置”；打开一份已有日报检查右侧工作区，再进入设置检查系统健康。验证界面无需触发新的 AI 生成或发送。

## 回滚

先停止现有服务，再恢复记录的代码版本和对应 `frontend/dist`。从发布前主线升级且没有结构变化时，一般无需覆盖数据库；早期安装若执行过迁移，要按其 manifest 核对兼容性。若确需恢复备份，必须先保留升级后的新增数据并单独核对，避免丢失新记录。最后仍通过原服务管理器启动并验证版本、就绪状态与界面。
