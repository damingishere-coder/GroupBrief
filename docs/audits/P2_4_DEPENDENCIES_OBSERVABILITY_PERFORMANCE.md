# P2.4 依赖、可观测性与证据化性能优化

日期：2026-08-25

## 结论

本轮没有盲目升级 React、提高生图并发或压缩 Prompt，而是只处理审计中已有直接证据的问题：Python 环境不可复现、分类日志可能未初始化、启动检查异常被吞、健康检查读写边界混乱、重复深度探测、两处前端 N+1、恢复扫描重复、固定轮询吞错和单主 Bundle。

## 依赖治理

- 新增 `requirements.lock`，记录当前 Windows / Python 3.12 已通过完整测试的直接与传递依赖版本；`requirements.txt` 和 `requirements-dev.txt` 继续表达兼容下限，安装时用 `-c requirements.lock` 收敛到验证组合。
- CI 改为使用 lock constraints，并通过 `pytest-cov` 生成 Sonar 已配置读取的 `coverage.xml`。
- `.gitignore` 明确排除 `.coverage`、`coverage.xml` 和 `htmlcov/`，不提交临时覆盖率产物。
- 新增 Dependabot 周更检查 pip、npm 与 GitHub Actions，只创建可审阅 PR，不自动做大版本升级。
- 当前 `pip check` 无破损依赖；`npm audit` 为 0 vulnerability。旧审计中的 Vite 5 漏洞结论已过期，当前为 Vite 6.4.3，本轮不再无依据升级。
- Starlette/FastAPI 仍发出 TestClient/httpx 迁移 warning；当前测试功能正常，等待上游支持路径明确后单独升级，不用忽略规则掩盖。

## 可观测性

### 日志与启动检查

- `setup_logging()` 不再因为 Uvicorn/宿主已安装 root handler 就提前退出；分类文件 handler 始终按目标路径幂等配置。
- 日志清理异常不再静默吞掉，会进入 `app.log`。
- 启动检查发生未捕获异常时，保存明确的 `ERROR` 检查项和错误摘要，并记录 exception；不再用空数组伪装成“没有问题”。

### 健康检查分层

- `GET /api/system/health`：纯 liveness，无外部调用、无写入。
- `GET /api/system/ready`：只读检查数据库、输出目录、默认模板和启动检查捕获状态；不调用 Provider、不创建探测文件。关键本地依赖失败时返回 HTTP 503。
- `GET /api/system/providers`：只读返回最近一次持久化结果，不再因 GET 执行外部检查或写库。
- `POST /api/system/providers/refresh`：显式执行 Provider 检查并持久化，每个 Provider 最多保留最近 100 条记录。
- `GET /api/v2/system/startup`：读取启动时保存的快照，不在浏览器刷新时再次执行启动探测。
- V2 深度健康响应结构保持不变，但 Codex summary、Codex image 和原生微信 sender 在同一请求内只生成一次 health report。

任务中心已改用轻量 readiness；设置中心仍保留用户明确触发的深度诊断，Dashboard 为发送按钮安全门禁仍读取真实 sender 健康状态。

## 有证据的性能优化

### 批量运行文件摘要

`GET /api/v2/runs` 新增可选 `include_files=true`。默认响应不变；显式启用时，每个 run 附带 allowlist 内的文件名。

- Tasks 和 ChatRecords 使用批量字段，不再对 N 条 run 再发 N 个详情请求。
- 如果连接到尚未支持新字段的旧后端，前端仍回退到原逐条详情请求，不牺牲兼容性。
- Fake API E2E 明确断言任务中心没有发出 `/api/v2/runs/{group}/{date}` 请求，也没有误用深度 health。

### 恢复扫描

`/api/v2/system/recovery` 现在只读取一次 run 快照，并把同一快照交给未完成扫描和完整性验证；此前会完整遍历并读取两次文件系统。

### 重新生图轮询

- 用完成后再排下一次的 `setTimeout` 替代固定 `setInterval`，避免慢请求重叠。
- 正常状态为 2 秒，Desktop fallback 为 5 秒；连续失败指数退避，最大 30 秒。
- 失败不再静默，页面会显示错误和下一次重试时间；成功后清除错误，状态机与人工发送门禁不变。

### 前端拆包

所有页面改为 React lazy route chunks。生产构建实测：

| 指标 | 修改前 | 修改后 | 变化 |
| --- | ---: | ---: | ---: |
| 主 JS | 469.02 kB | 192.99 kB | -58.9% |
| 主 JS gzip | 132.78 kB | 60.00 kB | -54.8% |

AIImages、Settings、Archive 等页面成为独立 chunk，hash 路由和页面行为不变。

## 测试与安全边界

- 新增 logging 已有 root handler、启动异常保存、readiness 200/503、Provider retention、深度报告复用、恢复单次扫描、批量 files、轮询退避和 Tasks Fake API E2E 测试。
- 覆盖率 XML 已成功生成：Python `app + scripts` 行覆盖率 78.88%（8,203 / 10,399）。覆盖率用于识别缺口，不设为了数字而写测试的门槛。
- 最新回归：后端全量 582/582；两个随机种子各 582/582；前端单测 13/13；Fake API E2E 4/4；compileall、带 `requirements.lock` 的 pip dry-run、`pip check`、`npm audit` 和正式 build 均通过。
- 全部浏览器测试拦截 `/api/**`；本轮未访问真实 8766，未调用真实 AI、生图、邮件或微信。

## 明确保留的非目标

- 图片单线程队列与 Codex 全局互斥是当前桌面资源保护，不提高并发。
- AI 有界重试和结果未知保护不动。
- RunStore 全量扫描未来仍需分页；当前规模下先消除重复扫描和 HTTP N+1，不引入缓存失效复杂度。
- 通用 request/job correlation ID 可在独立可观测性轮次增加；本轮先保证错误不会丢失、健康边界不会假成功。
