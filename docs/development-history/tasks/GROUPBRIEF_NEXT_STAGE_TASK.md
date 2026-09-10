# GroupBrief 自用可靠闭环与每周洞察实施任务

## 背景

GroupBrief 已具备六群日报生成、图片、归档和当天自动发送能力。2026-08-27 的可靠性整改把 watchdog 默认扩大到最近 30 天，真实服务启动后开始自动补历史任务并触发生图；同时完整测试与生产实例共用 `Local\\GroupBrief.Generation`，导致生产运行时测试无法执行。本任务按已确认方案将恢复窗口收敛为 48 小时、补齐状态真相和测试隔离，让群级配置真正生效，并在 14 天真实稳定性门槛后提供独立每周洞察。

## 目标

1. 最近 48 小时可自动恢复；更早欠账只读预览，确认后只恢复生成，绝不自动历史补发。
2. 每日状态必须以稳定群 ID 的预期任务清单为准，不得因“已发现的群都成功”误报完成。
3. 生产服务运行时，普通测试与仿真使用独立锁和临时运行根目录。
4. 群级统计规则、历史数据源偏好、摘要 Provider/模型和 Prompt Provider/模型均进入实际执行链并写入审计状态。
5. 旧版孤儿记录只读归档；不猜测关联、不删除、不参与新统计。
6. 每周洞察聚合既有日报工件，AI 失败仍有确定性文本和本地卡片；周一独立排队发送。

## 允许修改范围

- `app/`：恢复规划、调度、状态投影、群配置、Provider 路由、周报聚合/归档/调度/API。
- `frontend/`：恢复清单、群配置白名单、旧版历史归档、每周洞察只读页面及测试。
- `tests/`、`scripts/simulate_reliability.py`：隔离、故障注入、仿真和周报测试。
- 必要的兼容迁移、文档、`.env.example`、依赖约束和本任务文件。

## 禁止修改范围

- 不删除或批量改写 `data/`、`output/`、`runtime/` 中的真实历史和工件。
- 不自动解除 `SEND_RESULT_UNKNOWN`、`PROMPT_RESULT_UNKNOWN` 或其他结果未知状态。
- 不执行真实微信补发、邮件补发、真实历史重生成或付费 Provider 验收。
- 不引入云端多用户、Docker 原生微信发送、全面 Pipeline/CSS 重写或 React/Vite/TypeScript 大版本升级。
- 不读取、输出或提交 API Key、Token、密码、Cookie、`.env` 内容和完整聊天/Prompt。

## 已确定实现要求

### 恢复与状态

- 自动窗口为当前日期及前 1 天；更早任务只进入 backlog 预览。
- `GET /api/v2/recovery/backlog` 只读；`POST /api/v2/recovery/confirm` 使用版本时间/CAS，只允许选中任务恢复生成，不发送。
- 每日 manifest 保存稳定群 ID、显示名快照、规则、发送时间、目标身份和期望终态；旧状态惰性兼容。
- readiness 只描述服务/依赖；daily outcome 使用 `running/complete/partial/blocked/needs_attention`，完整性判断必须比较预期任务清单。

### 测试隔离

- 生产生成锁名保持 `Local\\GroupBrief.Generation`。
- 普通测试和仿真自动使用进程唯一的命名空间；专门的互斥测试可显式使用共享测试锁。
- 数据库、`output`、`runtime`、scheduler 状态和外部 Provider 默认隔离。

### 群级配置

- `schedule_rule` 仅支持 `weekday_default` 与 `daily_previous_day`。
- `provider_preference` 保留兼容但明确为历史数据源偏好；新 API/前端使用清晰名称。
- 摘要与 Prompt 分别保存 Provider/模型；空值继承全局。只允许健康检查/配置注册表返回的白名单组合。
- 请求值、实际 Provider/模型、fallback 原因写入 `run.json`。

### 每周洞察

- 周期为上一自然周周一至周日，基于非重叠日报周期和稳定发言人身份聚合。
- 每群最多一次 AI 周度叙述调用；失败时生成确定性统计文本和 Pillow 本地卡片。
- 周报拥有独立状态、claim、工件和归档；周一 08:30 进入现有串行发送边界，日报与周报互不冒充成功。

## 验收标准

1. GroupBrief 停止时与运行时，完整后端测试均通过；运行时测试不竞争生产锁。
2. 30 天 × 6 群仿真：预期任务零永久丢失、零重复生图、零重复发送、无无限重试；结果未知永久 hold。
3. 48 小时边界、过期确认、状态半写、图片落盘后崩溃、发送前/后失败和 Provider fallback 有故障注入证据。
4. 前端单测、生产构建、恢复/群配置/每周洞察 E2E 通过，390px 宽度可操作。
5. 旧数据库和旧 `run.json` 可读；迁移后 SQLite `integrity_check` 与 `foreign_key_check` 通过。
6. 实际上线只恢复当天自动任务，不执行历史发送；灰度群必须在执行时再次由用户确认。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe scripts\simulate_reliability.py --days 30 --seed 20260827 --groups 6
npm test --prefix frontend
npm run build --prefix frontend
npm run test:e2e --prefix frontend
git diff --check
```

## 返回格式

- 按恢复/状态、测试隔离、群级配置、历史归档、每周洞察报告行为变化和测试证据。
- 明确区分 Mock/仿真、真实本地运行和真实外部发送；不得把测试通过描述为真实送达。
- 报告 Alter 服务状态、Git 分支、提交哈希、远端地址和推送结果，以及尚未完成的 14 天观察项。
