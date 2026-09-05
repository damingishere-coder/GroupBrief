# 周一周报替代日报微信发送任务

## 背景

当前日报与周报拥有独立的周一 08:30 微信发送入口。开启周报后，两条任务会在同一时间运行，可能重复发送；重启恢复还可能重新安排周一日报。

## 目标

- 周一继续生成并归档日报，邮件逻辑保持不变。
- 周一微信发送改为上一自然周周报，不发送当天日报。
- 周二至周日继续发送日报。
- 重启恢复和生成完成后的补偿调度不得绕过替代规则。

## 允许修改范围

- `app/config/settings.py`
- `app/scheduler/manager.py`
- `app/scheduler/task_manifest.py`
- `app/scheduler/daily_v2_job.py`
- `app/scheduler/recovery_planner.py`
- `app/pipeline/daily_pipeline.py`
- `.env.example`
- 相关测试文件

## 禁止修改范围

- 真实 `.env`、数据库和生产输出状态。
- 微信真实发送、服务重启、部署和自动合并。
- 现有周报发送的结果未知人工锁定规则。

## 已确定实现要求

- 新增默认关闭的环境开关；只有周报生成、周报发送和替代开关同时开启时规则才生效。
- 周一日报清单期望终态为 `READY_TO_SEND`，并记录被周报替代的审计字段。
- 周一统一发送入口只运行周报发送；不得再注册同时间的独立周报发送 Cron。
- 周一日报的按需补偿不得创建日报发送任务。
- 周一重启或周报延迟完成后，应以一次性任务补齐周报生成/发送；发送结果未知仍只转人工复核。

## 验收标准

- 周一只调用周报发送，周二至周日只调用日报发送。
- 周一日报仍可完成生成态，不被运行状态误判为缺少发送。
- 调度器不存在两个周一 08:30 发送 Cron。
- 默认配置保持现状，不会意外开启周报。
- 针对性测试和完整相关测试通过。

## 测试命令

```powershell
python -m pytest tests/test_scheduler.py tests/test_recovery_planner.py tests/test_runtime_status.py tests/test_weekly_insights.py tests/test_v2_pipeline.py -q
```

## 返回格式

报告修改文件、测试结果、Git 分支/提交/PR、尚未执行的生产启用步骤。
