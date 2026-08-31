# GroupBrief PR #1 CI 合并任务

## 背景

PR #1 可合并且没有未解决评论，但 Windows CI 的前端单测环境和后端跨进程状态测试失败，禁止在检查未通过时直接合并。

## 目标

- 让前端 CI Node 版本满足当前锁定的 `jsdom` / `undici` 引擎要求。
- 修复 Windows 下运行状态文件原子替换偶发访问拒绝与损坏隔离问题。
- 在全部必需 CI 通过后使用 Squash 合并 PR #1。

## 允许修改范围

- `.github/workflows/ci.yml`
- `app/v2/run_store.py`
- `app/scheduler/daily_v2_job.py`
- `tests/test_cross_process_state.py`
- `app/core/path_security.py`
- `tests/test_path_security.py`

## 禁止修改范围

- 不跳过、删除或弱化 CI 检查。
- 不改变调度时间、生成、发送、恢复或 Provider 行为。
- 不启动生产服务，不生成图片，不发送微信或邮件。
- 不改写 Git 历史，不强推，不触碰原工作区已有审计改动。

## 已确定实现要求

- CI 使用精确 Node `22.22.2`。
- Windows named mutex 明确声明 ctypes 参数和返回类型，并显式处理等待失败。
- 状态文件写入使用 PID 与 UUID 组成的唯一临时文件；成功或异常后均清理残留。
- 原子替换仅对仍存在源文件的瞬时 `PermissionError` 做有限短退避重试；永久失败必须继续抛出。
- Windows 路径边界比较先规范化等价的 extended-length 前缀，不放宽导航、盘符、UNC 或符号链接边界。
- named mutex 使用规范化后的真实路径摘要，避免同一路径因 `\\?\` 表示差异生成两把锁。
- `DailyScheduleState` 与 `RunStore` 共用同一原子文本写入实现。

## 验收标准与测试

- `tests/test_cross_process_state.py` 连续多轮通过，且不残留临时文件。
- 完整后端 CI 等价测试通过。
- `npm ci`、`npm run build`、`npm test`、Playwright Fake API E2E 通过。
- 远端 PR #1 的必需检查全部成功，随后才执行 Squash 合并。

## 返回格式

报告修改文件、测试命令与结果、提交 SHA、远端 SHA、PR/CI/合并状态，以及原工作区是否保持不变。
