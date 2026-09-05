# 禁止统计兜底图进入日报生图流程

## 背景

2026-09-05 的两个群在真实图片事实校验失败后生成了 Level 3 Pillow 统计诊断图。后续重试仅按 PNG 可解码性复用了 `daily_image.png`，并将它记录为 `fallback_level=0`、`image_variant=normal`。另一个群因真实图及兜底图均包含无证据数字而没有图片。

## 目标

- 以后日报只接受正常 AI 生图或现有安全化 AI 生图流程的产物。
- 图片生成或事实校验失败时明确失败，不生成排行榜/统计表兜底图。
- 重试不得把旧 Level 3/Pillow 诊断图当作正常图片复用。
- 群级自定义关键词主题默认只作用于下一次新运行；界面可明确选择有限使用次数；额度消费后自动回到 `random_preset`。
- 修复和生产生效后，将 2026-09-05 的六个群全部切回 `random_preset`，重新生成并在逐图、逐目标验收后发送。

## 允许修改范围

- `app/image/`
- `app/pipeline/`（仅图片结果状态衔接需要时）
- `app/db/`、`app/api/groups.py`、调度任务快照（仅主题有限次数所需字段与原子消费）
- `frontend/src/` 中主题次数选择及对应 API 类型
- `tests/` 中对应图片生成与流水线测试
- 本任务说明文件

## 禁止修改范围

- 与本次六群随机重生成无关的历史 `output/`、日志和数据库内容
- 微信发送实现、群聊目标
- Provider、Codex 登录/认证方式
- 部署、服务重启、PR 合并

## 已确定实现要求

- 移除生产失败路径对 `render_local_infographic` 的调用，不再写出统计兜底 `daily_image.png`。
- 失败前清理本次失败路径遗留的不可交付目标文件，避免后续仅按图片格式复用。
- 现有文件复用必须结合运行元数据；Level 3、Pillow、`diagnostic_fallback` 或已知诊断图不得返回成功。
- 保留真实图片两次事实校验及现有安全化 AI 重试流程。
- 失败状态保持 fail-closed，不进入 `READY_TO_SEND`。
- 自定义主题保存时默认次数为 1；允许选择有限正整数。图片成功生成并记录结果时原子扣减一次，同一天运行的重试和 Prompt 重建不重复扣减，失败或结果未知不扣减；扣至 0 后群配置恢复每日随机。
- `ai_free` 与 `random_preset` 是持续模式，不消耗次数。
- 六群实际生成必须先清除当天旧图的可复用条件并保留发送锁；所有图片通过 PNG、尺寸、事实契约、归属路径与哈希验收后，才逐群验证发送目标并串行发送。

## 验收标准

- 图片失败时无统计兜底图，结果为失败且不可发送。
- 旧诊断图即使是可解码 PNG，也不会被 `existing_output_reused` 接纳。
- 正常已有图片仍可复用，不额外调用 Provider。
- 自定义关键词主题默认 1 次，可选多次；新运行只扣一次，重试不重复扣，耗尽后显示每日随机。
- 相关测试、完整图片/流水线测试、静态检查通过。
- 实际 diff 无范围外修改、敏感信息、TODO/debug 或临时文件。

## 测试命令

- `.venv\Scripts\python.exe -m pytest tests/test_v2_image_task.py tests/test_v2_pipeline.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_image_fact_verification.py tests/test_runtime_status.py tests/test_v2_ui_router_contract.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_group_image_theme_batch_api.py tests/test_generation_concurrency.py tests/test_daily_random_theme.py -q`
- `npm --prefix frontend test -- --run`
- `npm --prefix frontend run build`
- `.venv\Scripts\python.exe -m compileall app tests`
- `git diff --check`

## 返回格式

- 根因证据
- 修改文件与行为变化
- 测试命令和结果
- Git 分支、提交、Push、PR、CI 状态
- 六群随机主题配置、生成验收、逐群发送证据；生产运行是否已生效
