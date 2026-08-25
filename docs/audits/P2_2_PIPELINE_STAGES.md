# P2.2 Pipeline 分阶段拆分验收

## 结论

`DailyPipeline` 继续作为原 API、CLI、调度器和恢复流程的兼容 Facade；内部单群生成、图片任务和微信发送已拆成独立阶段模块。此次是行为保持型重构，没有修改数据库、`run.json` 契约、状态值、Provider、Prompt、图片生成器或 Sender。

## 拆分结果

| 项目 | 拆分前 | 拆分后 |
| --- | ---: | ---: |
| `daily_pipeline.py` | 1,498 行 | 852 行 |
| `_generate_one` | 378 行 | 33 行 Facade |
| `_send_one` | 249 行 | 27 行 Facade |

新增模块：

- `stage_result.py`：显式区分“继续下一阶段”和“携带业务结果终止”。
- `generation_stages.py`：运行初始化/防重、消息快照、显式刷新、排行榜、Prompt claim/commit/unknown、图片决策、耗时收口。
- `image_stages.py`：图片任务构造、串行队列、结果字段落盘、`IMAGE_READY → READY_TO_SEND` 收口。
- `delivery_stages.py`：发送 claim、文件预检、文字、图片、部分成功恢复、unknown hold、`SENT` 收口。

## 保留的兼容边界

- `DailyPipeline` 构造参数及 `generate_all`、`send_due`、`force_generate`、`force_send`、`rebuild_prompt_from_snapshot` 未改变。
- `_generate_one_safe`、`_generate_one`、`_run_image_when_ready`、`_make_image_job`、`_image_hook`、`_run_image_jobs`、`_send_one`、`_finish_unknown_send`、`_sync_group_names`、`_load_groups` 仍存在。
- 图片仍通过 `SerialImageQueue` 严格串行，现有 hook 仍可注入。
- Prompt 的 `claim → record → commit`、`result_recorded` 恢复和 `result_unknown` hold 未改变。
- 微信发送仍保留 claim/lease、文字成功后图片失败只补图片、提交后结果未知禁止重复发送、人工审核和逾期确认。
- 群级失败隔离、并发上限、结果稳定排序和 V1 冻结未改变。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 阶段/Pipeline/恢复/调度/幂等定向回归 | `122 passed` |
| 后端正常顺序全量 | `570 passed` |
| 随机顺序 seed `20260825` | `570 passed` |
| 随机顺序 seed `8675309` | `570 passed` |
| Python compileall | 通过 |
| 前端单元测试 | `7 passed` |
| 前端正式构建 | 通过 |

唯一 warning 仍是已知 Starlette/httpx 弃用提示，归入 P2.4 依赖治理。

## 明确未做

- 没有把 V1 `ReportService` 合并进 V2。
- 没有重写状态机、引入事件总线或新框架。
- 没有改变调度所有权、API 同步执行方式或数据库事务模型。
- 没有启动/停止 8766，也没有调用真实 AI、邮件或微信。
