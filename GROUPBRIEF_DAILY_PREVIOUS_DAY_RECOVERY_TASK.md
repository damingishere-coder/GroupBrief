# GroupBrief 每日前一日规则修复与 2026-08-29 补跑任务

## 背景

2026-08-29 00:15 调度器已触发，但六个启用群均因 `weekday_default` 周末规则被过滤，
当天清单为空并以 `not_run` 结束。这与“每天生成前一天群报”的产品约定不一致。

## 目标

1. 将应用、新建群、空值回退和示例配置的默认统计规则统一为 `daily_previous_day`。
2. 保留 `weekday_default` 作为用户显式选择的兼容规则。
3. 在生产 SQLite 在线备份后，用单事务把群 23–28 切换为 `daily_previous_day`。
4. 通过带 `state_version` CAS 的恢复入口修复 2026-08-29 空清单并补跑六群生成。
5. 补跑不发送邮件、不手工调用微信发送；微信仍由既有 08:30 `send_due` 流程处理。

## 允许修改范围

- 群配置默认值、统计周期默认值、任务清单空值回退。
- Dashboard/群详情的默认规则展示和示例配置。
- 调度状态 CAS 更新与空清单显式恢复 API。
- 与上述行为直接相关的后端、前端测试。

## 禁止修改范围

- 不改变显式 `weekday_default` 的工作日语义。
- 不修改群名称、发送目标、发送时间、发送开关、Prompt、图片主题或排行榜规则。
- 不删除或覆盖历史 `run.json`、发送凭证、图片、Prompt 或聊天数据。
- 不手工提前发送、不重发历史内容、不访问或写入任何密钥。

## 已确定实现要求

- 默认值统一为 `daily_previous_day`；显式工作日规则仍可选。
- 空清单恢复只允许在旧状态为 `expected_groups=[]`、`generation_status=not_run`、
  调用方提供的 `state_version` 匹配且当天不存在群级运行记录时执行。
- 恢复前按当前数据库重新计算预期群，并校验群 ID 集合完全匹配。
- 状态更新必须在生成锁与状态文件锁下原子完成，记录恢复审计字段。
- 生产数据库使用 SQLite online backup、`BEGIN IMMEDIATE`、完整性和外键检查。

## 验收标准

- 周六/周日默认规则也运行，且只覆盖前一自然日。
- 显式 `weekday_default` 周末仍跳过，周一仍覆盖周五至周日。
- 新建群、模型默认、旧库补列、空值回退与前端新建表单均为 `daily_previous_day`。
- 生产群 23–28 的规则均为 `daily_previous_day`，其他配置不变。
- 2026-08-29 清单包含且仅包含群 23–28，统计窗口为 2026-08-28 全天。
- 六群生成达到可信终态；未手工调用邮件或微信发送。
- 后端测试、前端测试、前端构建、Python 编译和 `git diff --check` 通过。

## 测试命令

- `.\.venv\Scripts\python.exe -m pytest tests -q`
- `npm test -- --run`
- `npm run build`
- `.\.venv\Scripts\python.exe -m compileall app scripts`
- `git diff --check`

## 返回格式

- 根因与修复范围。
- 生产备份路径、数据库更新前后值和完整性检查。
- 2026-08-29 六群生成及 08:30 待发送状态。
- 测试、提交哈希、分支和推送结果。
