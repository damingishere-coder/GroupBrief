# GroupBrief 图片自动化恢复任务

## 背景

2026-08-31 的六群日报中，3 群图片成功，Grok 因 Codex 返回无效 JSON 被错误归入结果未知，米游涩泛 1.1 与 Eason 的 AI 图片被事实校验拦截；Eason 的第二次图片调用又遇到明确网络失败。现有流程没有在这些“结果已知但不可用”的终态统一落到本地安全信息图，导致整批生成和后续发送被阻断。

## 目标

- 已收到但格式无效的 Codex JSON 视为已知无效响应，不切换收费 Provider、不自动发起第二次外部调用，并允许 Pipeline 使用本地信息图兜底。
- 严格图片事实校验连续失败，或质量重试明确失败时，生成并校验本地信息图。
- 保留真正结果未知时的人工暂停，禁止自动重复外部调用。
- 消除已确认的 OCR 数字误判：结构化头部数字、`块/元`货币同义和纯零符号误识别。
- 恢复 2026-08-31 未完成群，图片成功后再进入发送；不重生成已成功图片。

## 允许修改范围

- `app/providers/ai/base.py`
- `app/providers/ai/codex.py`
- `app/image/fact_verification.py`
- `app/image/image_task.py`
- 对应的 `tests/` 测试文件
- 本任务文件

## 禁止修改范围

- 不更换 Codex 模型、登录方式、认证或 Provider 配置。
- 不读取或提交 `.env`、Token、Cookie、浏览器数据。
- 不放宽未知图片结果、未知 Prompt 结果和未知发送结果的 fail-closed 规则。
- 不改排行榜、摘要内容、群配置、发送目标和已成功图片。
- 不自动合并 PR，不强推，不清理用户已有改动。

## 已确定实现要求

1. 新增“外部调用已返回但响应不可解析”的明确异常类型；它既不是未提交，也不是结果未知。
2. Codex JSON 无效或不是对象时抛出该异常，禁止调用备用 Provider；PromptBuilder 把它收敛为普通失败，由现有本地信息图路径处理。
3. 严格图片校验的第二次结果只有在结果已知时才允许本地兜底；结果未知继续失败关闭。
4. 数字事实校验只补充确定性结构化头部证据，并做窄范围规范化，不把整个 AI Prompt 当作数字事实来源。
5. 运行恢复前备份当天权威 `run.json`/调度状态；发送前逐群校验微信目标和图片文件哈希。

## 验收标准

- 无效 Codex JSON 不进入 `PROMPT_RESULT_UNKNOWN`，不调用 DeepSeek 备用，最终能走本地图片兜底。
- 两次严格事实校验失败或第二次明确网络失败时，任务得到可解码、事实校验通过的本地 PNG。
- 第二次图片结果未知时不生成替代图片、不再次调用外部图片 Provider。
- Eason 群名 `4.1`、`38块/38元`以及 OCR 纯零误识别均有回归测试。
- 聚焦测试与完整后端测试通过；`git diff --check` 通过。
- 今日六群最终均有有效图片；发送只在目标匹配后执行，并以 `run.json` 与 UI 发送证据为准。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_codex_summary_provider.py tests\test_image_fact_verification.py tests\test_v2_image_task.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## 返回格式

- 根因与修复点
- 测试命令、通过数和失败证据
- 今日逐群图片/发送终态
- Git 分支、提交、远端 SHA、PR 和 CI 状态
