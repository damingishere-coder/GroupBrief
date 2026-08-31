# 2026-08-26 自动化恢复链路加固

## 背景

2026-08-26 的每日任务暴露出两类独立问题：Codex 生图已经落盘但进程超时且最终结构化回执缺失，任务无法安全认领图片；微信文字实际提交后，固定延迟截图没有观察到输入区暂存状态，发送结果被保守标记为未知并暂停图片阶段。

## 目标

- 使用同一 Codex exec 线程标识安全恢复超时后已经落盘的唯一图片，不猜图、不重复付费生成。
- 将微信提交验证改为有界轮询，并在按 Enter 前强制证明输入区已经暂存内容。
- 为文字发送未知增加带并发保护的人工消歧，只更新阶段检查点，不在消歧接口内发送。
- 让 Dashboard、任务中心和调度日志准确展示暂停任务和恢复来源。
- 保持逐群邮件账本幂等；仅对本次新恢复的群重新进入邮件收口。

## 允许修改范围

- `app/image`、`app/pipeline`、`app/scheduler`、`app/sender`、`app/v2`、`app/api`、`app/config`、`app/db` 中与本任务直接相关的代码。
- `frontend/src` 中 Dashboard、任务中心、设置与 API 类型。
- 对应后端与前端测试、本文档。

## 禁止修改范围

- 不修改 2026-08-26 的 `output`、调度账本、邮件账本或发送时间戳。
- 不触发真实 Codex 生图、SMTP 邮件、微信消息、部署或数据库迁移。
- 不读取或写入 API Key、Token、Cookie、密码、`.env` 内容和浏览器数据。
- 不改变 Codex Provider、登录方式或认证配置。

## 已确定实现要求

- `codex exec --json` 的 `thread.started.thread_id` 必须流式、原子写入 attempt manifest；结构化回执优先，线程目录候选仅在新增、唯一、路径归属、尺寸和哈希全部通过时可信。
- 没有可信证据时保持 `result_unknown`，不得启动下一次外部生成。
- 微信暂存轮询默认 5 秒、提交验证默认 8 秒、轮询间隔默认 0.2 秒；暂存未观察到时不得按 Enter。
- 人工消歧请求必须携带 `expected_send_unknown_at`，只支持文字未知；`text_sent` 续跑图片阶段，`not_sent` 重置文字阶段，两者均不直接发送。
- 新字段向后兼容；Dashboard 增加 `held` 计数并展示 `send_error`。
- 2026-08-26 不补发任何缺失内容。

## 验收标准

- 超时进程创建的同线程唯一有效图片能被认领；目录不匹配、旧文件、多候选和无效尺寸均保持未知。
- 暂存延迟可以成功等待；暂存未出现绝不提交；提交后歧义仍 fail closed。
- 人工消歧具备时间戳 CAS、幂等和阶段续跑测试。
- Dashboard、任务中心、API 和调度日志测试通过。
- 所有测试使用临时目录和假发送器，不产生真实外部动作。
- 最终检查工作区 diff、依赖、调试残留和范围外修改。

## 测试命令

- `.venv\\Scripts\\python.exe -m pytest tests/test_v2_image_task.py tests/test_v2_wechat_native.py tests/test_v2_pipeline.py tests/test_v2_ui_router_contract.py tests/test_scheduler.py tests/test_send_daily_email.py -q`
- `.venv\\Scripts\\python.exe -m pytest tests -q`
- `npm test -- --run`
- `npm run lint`
- `npm run build`

前端命令从 `frontend` 目录运行；最终具体脚本以 `package.json` 为准。

## 返回格式

最终报告应包含：两类根因、实际修改、测试命令与结果、运行时健康验证、未执行的真实外部动作、Git 提交哈希、分支、远端地址和推送结果。
