# 茶馆 Level 3 诊断图禁发任务

## 背景

茶馆日报在 Prompt 连续校验失败后生成了 Pillow Level 3 本地信息图。该文件本应仅供诊断，但旧流程把它记录为生图成功并推进到自动发送，最终在 2026-09-03 08:36 发送。Dashboard 同时以 `object-fit: cover` 裁切了竖版预览。

## 目标

- 保留 PR #16 已有的按真实 `sender_id` 去重、参与者补足和越权消息 ID 校验。
- 将 Level 3/Pillow 兜底定义为“已保存诊断产物的图片生成失败”，不得推进到发送就绪。
- 在发送扫描、原子 claim、发送前预检三层拒绝诊断图，统一错误码 `IMAGE_FALLBACK_NOT_SENDABLE`。
- 让 Dashboard 同时保留历史发送事实并明确展示图片失败、诊断图不可发送和完整竖图预览。

## 允许修改范围

- `app/image/`、`app/pipeline/`、`app/v2/`、`app/scheduler/` 中与图片状态和发送门禁直接相关的代码。
- `app/api/v2_ui_read.py` 的 Dashboard 只读投影。
- `frontend/src/api.ts`、`frontend/src/pages/v2/Dashboard.tsx`、`frontend/src/styles.css`。
- 与上述行为直接相关的后端测试、前端单测和 Playwright 测试。
- 本任务说明文件与 PR #16 描述。

## 禁止修改范围

- 不修改数据库结构或执行迁移。
- 不读取或修改 `.env`、认证、Provider、登录方式及任何密钥。
- 不修改既有 `output/`、`run.json`、PNG、发送记录或生产日志。
- 不调用生成、恢复、发送、重发、发布、部署或服务重启接口。
- 不改动主工作树的 `.codemap` 和复检报告。

## 已确定实现要求

1. Level 3/Pillow 允许落盘诊断 PNG、失败原因及 SHA-256，但任务返回失败并停在图片阶段 `FAILED`。
2. 自动重试仍受现有预算限制；诊断 PNG 不得被“已有有效图片”逻辑误判为正常成功。后续真实图片成功时清除兜底元数据并恢复正常流程。
3. 发送扫描、claim 和发送前预检均拒绝 `image_fallback_level >= 3` 或 `image_variant=pillow`，且不得调用文字或图片发送器。
4. Dashboard API 返回 `image_status`、`image_fallback_level`、`image_fallback_reason`、`image_variant`、`image_delivery_eligible`。
5. 历史 `SENT + Level 3` 保留“已发送”，同时将图片节点投影为失败，卡片显示“图片生成失败（已发送）”和“诊断图不可发送”，且不出现重发入口。
6. Dashboard 图片预览使用完整适配；普通有效图片和关闭生图的流程保持不变。

## 验收标准

- PR #16 的人物去重/补足/消息归属测试继续通过。
- Prompt、Provider、事实校验失败生成诊断图后，状态为图片失败而非待发送。
- 构造遗留 `READY_TO_SEND + Level 3/Pillow` 时，扫描、claim、发送前预检均返回统一错误码，发送器调用数为零。
- 真实图片成功、关闭生图、诊断后重试成功等流程通过。
- 历史 `SENT + Level 3` 的 API 与 UI 同时呈现发送事实和图片失败，无发送按钮。
- 后端相关测试和全量测试、Python 编译检查、前端单测、Playwright、前端构建及 `git diff --check` 全部通过。

## 测试命令

```powershell
python -m pytest tests/test_v2_prompt_builder.py tests/test_v2_image_task.py tests/test_v2_pipeline.py tests/test_runtime_status.py tests/test_v2_ui_router_contract.py -q
python -m pytest tests -q
python -m compileall -q app scripts tests
npm test
npm run test:e2e
npm run build
git diff --check
```

## 返回格式

返回修改摘要、根因闭环、测试命令与结果、实际 diff 范围、提交 SHA、远端 SHA、PR #16 地址与 CI 状态；明确说明未重发、未重启、未合并。
