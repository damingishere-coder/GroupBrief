# 2026-08-27 Prompt 生成可靠性修复

## 背景

2026-08-27 自动生成中出现两类 Prompt 阶段故障：

1. `Grok App 交流群` 的 Codex GPT 调用超过 240 秒，进入 `PROMPT_RESULT_UNKNOWN`；现有 Dashboard 仍显示“立即生成”，但后台会永久拒绝再次领取，缺少人工消歧闭环。
2. `米游涩泛二次元同好摸鱼群2.3` 的漫画编辑 JSON 连续三次返回超过 48 字的真实气泡；系统提示没有明确气泡上限，也没有对可安全缩短的真实连续片段做确定性归一化。

## 目标

- 降低 Codex 文本整理调用因继承用户级推理配置和过短超时导致的超时概率。
- 保留“结果未知禁止自动重试”的安全语义，并提供显式、CAS 保护的人工确认入口。
- 让超过 48 字但可从真实原话中提取完整连续短句的气泡稳定通过；不可安全缩短时仍拒绝。
- Dashboard 对 Prompt 结果未知显示准确操作，不再提供注定失败的普通“立即生成”。

## 允许修改范围

- `app/providers/ai/codex.py`
- `app/config/settings.py`
- `app/db/repository.py`
- `app/api/settings.py`
- `app/ai/poster_copy.py`
- `app/v2/run_store.py`
- `app/pipeline/daily_pipeline.py`
- `app/api/v2_ui.py`
- `frontend/src/api.ts`
- `frontend/src/pages/v2/Dashboard.tsx`
- 与上述行为直接相关的测试、文档和前端样式（仅在确有需要时）

## 禁止修改范围

- 不修改 Codex 登录方式、认证文件、API Key、Token、Cookie 或 `.env` 内容。
- 不自动重跑当天真实 Codex/GPT/ImageGen 生成，不发送微信，不发送邮件。
- 不改变发送结果未知的 fail-closed 语义。
- 不修改历史 `run.json`、图片、排行榜或聊天快照。
- 不重置、清理或覆盖用户已有 Git 工作。

## 已确定实现要求

- Codex 文本调用继续固定使用已配置的 OpenAI 模型，显式隔离用户级非必要运行配置，并使用适合结构化文本整理的受控推理强度。
- 默认文本调用超时从 240 秒提高到 600 秒；运行中持久化设置通过正式设置 API 单独更新，不直接改 SQLite。
- 超时、非零退出、缺少最终文本仍视为结果未知，禁止自动切备用或自动重试。
- Prompt 未知消歧必须校验当前 operation id，且消歧本身不调用外部模型；只有用户随后确认生成时才新建调用。
- 真实气泡自动缩短只能取自同一 speaker 的原消息连续片段，不得改写、拼接或添加省略号。
- 无法找到不超过 48 字的完整连续短句时继续返回合同错误。

## 验收标准

- Codex 命令包含显式隔离/推理参数，模型、认证目录和只读 sandbox 保持不变。
- Prompt 未知状态无法被普通 force 绕过；错误 operation id 的人工消歧失败；正确确认后可重新领取，但确认动作本身零外部调用。
- Dashboard 能识别 `prompt_hold`，显示清晰风险说明并要求确认。
- 超长多句真实原话可确定性选出不超过 48 字的连续完整短句；无安全边界的超长原话仍失败。
- 针对性后端测试、`pytest tests -q`、前端测试和生产构建通过。

## 测试命令

```powershell
.venv\Scripts\python.exe -m pytest tests\test_codex_summary_provider.py tests\test_p14_generation_idempotency.py tests\test_v2_prompt_builder.py tests\test_v2_ui_router_contract.py -q
.venv\Scripts\python.exe -m pytest tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

## 返回格式

- 根因与修复摘要
- 测试/运行时证据
- 未执行的真实外部动作
- Git 提交、分支、远端与推送结果
