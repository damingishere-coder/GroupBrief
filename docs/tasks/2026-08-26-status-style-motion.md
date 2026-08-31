# GroupBrief 状态闭环、风格中心与全站克制动效改造

## 背景

当前仪表盘会把发送结果无法自动确认的任务持续显示为“暂停待核对”，刷新不会把用户在微信中手工完成的发送写回 `run.json`；排行榜与图片分散展示，聊天记录与排行榜也没有默认筛选当天。AI 图片风格选择器使用大型下拉层，缺少可核对的示例图。全站只有零散 CSS 过渡，没有统一的减弱动态效果支持。

## 目标

- 让 Dashboard 支持指定运行日期、排行榜预览和每群完整任务卡。
- 增加不触发微信发送的人工状态处理闭环，并保留冲突保护与审计历史。
- 调整主导航顺序，聊天记录和排行榜默认使用上海本地当天。
- 将图片风格选择器改为有草稿/确认语义的风格中心，并提供 22 张本地 WebP 示例图。
- 接入 `motion@^13.1.1`，只在页面、弹窗和内容切换等有业务意义的位置使用克制动效。
- 对减少动态效果、键盘焦点、Escape、遮罩关闭、`aria-live` 和窄屏布局提供完整降级。

## 允许修改范围

- `app/api/`、`app/pipeline/`、`app/v2/` 中与本次 API、状态和主题目录直接相关的文件。
- `frontend/` 的源码、测试、依赖清单、锁文件与本次新增静态资源。
- `tests/` 中本次功能的后端测试。
- 本任务文档。

## 禁止修改范围

- 主题解析规则、Prompt 事实约束、图片生成模型和真实微信发送实现。
- 现有历史 Prompt、历史图片和非目标群任务状态。
- 数据库结构、生产数据库和远程服务器。
- 任何 secrets、`.env`、Cookie、浏览器数据或用户未授权的外部发送。

## 已确定实现要求

- `run.json` 继续作为任务状态权威来源；人工处理使用 `expected_updated_at` 做 CAS。
- 新接口只写状态和审计历史，绝不调用发送器；支持 `all_sent`、`text_sent`、`not_sent`。
- `MotionConfig reducedMotion="user"` 与 `LazyMotion + domAnimation` 统一接入；动画以 160–240ms 的 opacity/transform 为主。
- 长列表不做逐项 stagger，不添加持续闪烁、弹跳、数字滚动、虚构进度或装饰性循环。
- 风格修改先保存在模态草稿中，只有“使用这个风格”才提交；关闭和取消不保存。
- 示例图不包含真实群数据、文字、品牌、版权角色或水印；压缩为 WebP 后单张目标不超过 300KB，总体不超过 8MB。

## 验收标准

- Dashboard 指定日期能正确返回任务统计、Top 5 排行和图片；人工确认后统计即时更新。
- 人工确认写入 `send_resolution_history`；过期 `expected_updated_at` 返回冲突；重复确认不触发发送。
- 主导航顺序正确；聊天记录和排行榜默认显示上海本地当天，且可以清空查看全部历史。
- 风格中心四页签、搜索/分类、22 个风格、示例/色板、草稿确认和取消语义可用。
- 普通动画和 reduced-motion 下页面最终 DOM、Toast、弹窗、内容切换与焦点行为一致。
- Motion gzip 增量目标不超过约 30KB；窄屏、长消息列表和图片页无明显布局抖动或点击阻塞。
- Eason 当天 `run.json` 先备份，再以 `all_sent` 核对为已发送；不调用微信发送，Dashboard 为已发送 6、暂停待核对 0。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v2_ui_router_contract.py tests\test_v2_pipeline.py tests\test_daily_random_theme.py -q
Set-Location frontend
npm test
npm run build
npm run test:e2e
```

## 返回格式

- 列出核心功能、测试/构建/E2E 结果、资源体积与 Motion gzip 增量。
- 报告服务重启后 8766 的监听归属和真实页面核验结果。
- 报告 Eason 备份路径、状态变更与“未触发微信发送”的证据。
- 报告 Git 分支、提交哈希、远端地址和普通推送结果。

## 完成记录

- 后端完整测试：592 passed；前端单元测试：20 passed；E2E：10 passed。
- 前端构建通过，主 JS gzip 90.06KB、CSS gzip 18.14KB；相对改造前主 JS 约增加 30.07KB。
- 22 张 WebP 示例图均为 1024×1536，合计 4,855,496 bytes，最大 300,318 bytes。
- Alter 登记 `GroupBrief-Backend` 已按精确 ID 重启；8766 监听进程祖先为 Alter daemon。
- Eason 当天状态备份：`output/Eason张UED-4群🤘/2026-08-26/run.json.before-manual-all-sent-20260826-163028.bak`，SHA-256 `9A262868AD653D8C4BE3EC4CD99997AB64675AB00D160524F32FA0A169BC6091`。
- `all_sent` 人工核对完成：`SENT`、`manual_user_confirmed`，Dashboard 为已发送 6、暂停待核对 0；接口未调用微信发送器。
