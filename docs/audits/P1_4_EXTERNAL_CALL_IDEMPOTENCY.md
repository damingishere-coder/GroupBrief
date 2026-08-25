# P1.4 AI 与邮件外部调用幂等

日期：2026-08-25

## 结论

AI Prompt、生图和 SMTP 发送现在都遵循同一条安全边界：只有能够确认“请求尚未提交”的失败才允许自动重试；一旦请求可能已经到达外部服务但结果无法确认，立即进入 `result_unknown`/人工 hold，禁止自动切换 Provider、重复生成或重复发送。

```text
准备/领取操作
    ├─ 明确未提交失败 → 可受控重试
    ├─ 外部调用成功 → 先记录结果/发送凭据，再推进业务状态
    └─ 已提交但结果未知 → hold，必须人工核对
```

## 整改前风险

- Codex CLI 会在超时、非零退出或无输出后自动重试，并可能切换 DeepSeek；第一次调用实际成功但本地没拿到结果时会重复扣费。
- DeepSeek 把读取超时、响应解析错误等“是否已提交未知”的异常当成普通重试条件。
- Prompt 没有外部操作 claim，也没有“先记录付费结果、再写最终文件”的恢复点；进程在两者之间退出会再次调用模型。
- 图片生成虽有 manifest，但“进程结束且没有候选文件”会继续下一次外部调用。
- 邮件按群循环发送，没有逐封稳定身份和交付账本；中途失败后重跑会重复发送前面已经成功的群。
- 邮件子进程只返回粗粒度成功/失败，调度器无法区分“未提交失败”和“提交结果未知”。

## 修改

### AI Provider

- 新增 `ExternalCallNotSubmittedError` 与 `ExternalCallResultUnknownError`，把外部调用失败按是否可能已提交分类。
- Codex CLI 只在二进制不存在或进程无法启动时视为未提交；超时、非零退出、缺输出、空输出、非法 JSON 都视为结果未知，不再内部重试或切 DeepSeek。
- DeepSeek 仅对连接前失败、明确 HTTP 429/503 做受控重试；其他 5xx、读取/写入超时、连接中断、HTTP 200 响应不可解析均进入结果未知。
- 日志只记录稳定 `request_id`、模型和错误类型，不记录 Provider 原始响应或聊天正文。

### Prompt 操作恢复

- `run.json` 新增逐次 Prompt 操作的 operation id、输入指纹、started/result_recorded/succeeded/unknown 状态和人工 hold。
- 外部调用前原子 claim；相同运行发现 started 且无可信结束时直接转 unknown，`force` 也不能绕过。
- 模型返回后先把 Prompt、元数据和 SHA-256 记录到 `run.json`，再原子替换最终 Prompt 文件。
- 若在结果记录后、最终文件提交前退出，恢复流程直接提交已记录结果，不再调用 AI。
- Provider 明确返回结果未知时，API、恢复扫描和调度均显示 `PROMPT_RESULT_UNKNOWN`，不降格成可自动重试的普通失败。

### 图片生成

- 在全局图片锁内部再次检查有效输出，防止两个进程先后等待锁后各调用一次 Provider。
- 相同输出在非 force 模式下直接复用。
- manifest 已是 `result_unknown`，或外部进程已经启动但没有可信候选文件时，禁止第二次外部调用并保留人工复核状态。
- manifest 已确认完成但正式图片后来缺失时，普通恢复也不会重新付费生成；只有显式 force 才能启动新调用。
- 兼容现有两参数图片生成器；只有支持 `force` 的生成器才传递该参数。

### 邮件发送

- 新增稳定语义指纹和稳定 `Message-ID`；MIME 随机 boundary 不影响同一封业务邮件的身份。
- 在 `output/.email-delivery/` 建立逐封 JSON 账本，状态为 prepared/submitting/sent/failed_before_submit/unknown。
- 逐封发送全程持有进程内锁和 Windows 命名互斥锁；同一邮件已 sent 时直接跳过。
- 只有连接、TLS 或认证阶段失败可以重试；进入 `send_message` 后的异常、部分收件人拒绝或中断统一为 unknown，后续运行禁止自动重发。
- SMTP `quit` 在 sent 持久化之后执行；关闭连接失败不会触发重复发送。
- 日报脚本按 sent/already_sent/failed_before_submit/unknown 汇总，使用稳定退出码：0 成功、2 部分、3 结果未知、1 失败。
- 有群因报告或附件缺失而跳过时，日报脚本和 V1 邮件服务不再把其余群发送成功误报成全量成功。
- V2 调度把退出码 3 映射为 `EMAIL_RESULT_UNKNOWN`、`email_hold=true`，不能再把它记成成功。

## 保留边界

- 微信发送已有 claim/lease/unknown 账本，本轮不重复改写。
- SMTP 服务端没有通用 Idempotency-Key；稳定 Message-ID 便于对账，但真正防重依赖本地账本和 fail-closed 状态。
- V1 数据库级生成唯一约束留到 P1.5 双轨冻结时处理，避免在双轨仍活动时引入新的状态竞争。
- unknown 不自动猜测成功或失败；人工核对后的解除流程应作为独立、可审计操作实现。

## 验证

- P1.4 相关回归套件：165 项通过（12.72 秒）。
- 图片专项：30 项通过（1.94 秒）。
- 邮件专项：43 项通过（2.44 秒）。
- 项目 `tests/` 最终全量：559 项通过、1 项失败、1 条弃用 warning（25.59 秒）。
- 唯一失败为 `test_five_groups_overlap_with_limits_order_and_failure_isolation` 的机器计时阈值：业务结果和并发上限断言均通过，最终实测 0.470 秒，高于固定 0.45 秒；归入 P2.1 的随机顺序/时序测试稳定性整改，不在本轮放宽断言。
- Python compileall、前端 production build 和 `git diff --check` 通过。
- 测试全部使用 Fake Provider/Fake SMTP；未调用真实 AI、SMTP、微信或其他外部服务。
- 测试产生的临时邮件账本已核对并删除，真实 `output` 未留下测试交付记录。

## 部署说明

8766 常驻进程需要在工作区达到下一个安全检查点后由 Alter 管理器安全重启，才能加载本轮代码。本轮不直接触发真实 AI、邮件或微信来做验收。

## 回滚

- 没有数据库 Schema 变更，可整体 revert 本次提交。
- 已产生的生产邮件账本属于防重审计记录，代码回滚时也不应删除。
- 回滚会恢复自动重试/切备用和逐群重复邮件风险，只适合作为紧急代码回退。
