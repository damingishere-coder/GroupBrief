# P2.3 API 与前端 God Module 拆分

日期：2026-08-25

## 目标与边界

本轮只按已有职责拆分两个高耦合模块，不改变 Pipeline、RunStore、数据库、Provider、真实生图或发送流程。

- 保留全部 `/api/v2` 路径、HTTP 方法、状态码、响应结构和默认 OpenAPI operation ID。
- 保留 `app.api.v2_ui` 的 `router`、`RunPromptUpdateBody`、`RetryBody`、`update_run_prompt`、`restore_run_prompt`、`retry_failed` 与 `_store` 兼容入口。
- 保留 `AIImages` 默认导出、`/#/images` 路由、原有 `.ai-images-*` CSS 类、可见文案、aria 标签和 API 封装。
- 验证只使用隔离测试或前端 Fake API，不连接真实 8766，不调用真实 AI、生图、邮件或微信。

## 拆分结果

### 后端 API

| 模块 | 职责 | 行数 |
| --- | --- | ---: |
| `app/api/v2_ui.py` | 聚合入口、系统诊断、Pipeline 命令、兼容导出 | 333 |
| `app/api/v2_ui_common.py` | 请求模型、RunStore/时区/路径校验公共边界 | 86 |
| `app/api/v2_ui_read.py` | Dashboard、运行列表、归档、输出文件读取 | 356 |
| `app/api/v2_ui_images.py` | 图片主题、运行 Prompt、重建与重新生图命令 | 321 |

原 `v2_ui.py` 为 866 行且同时承担查询、归档、图片编辑、系统探测和 Pipeline 命令；拆分后最大文件为 356 行，聚合入口降为 333 行。总行数增加来自明确的模块接口、独立 imports 和说明，不是新增业务分支。

### 前端 AI 图片工作台

| 模块 | 职责 | 行数 |
| --- | --- | ---: |
| `AIImages.tsx` | 页面组合与顶层加载/失败边界 | 29 |
| `ai-images/useAIImageCatalogs.ts` | 群、主题、默认 Prompt 一次性并行加载 | 71 |
| `ai-images/ImageStylePanel.tsx` | 群级默认生图风格状态、预览与保存 | 242 |
| `ai-images/useAIImageRuns.ts` | 运行选择、详情、Prompt 命令、轮询和发送确认 | 332 |
| `ai-images/AIImageRunWorkspace.tsx` | 运行筛选、详情、图片和操作区视图 | 144 |
| `ai-images/model.tsx` | 状态标签、错误说明、稳定 key 与 Prompt 预览 | 98 |

原 `AIImages.tsx` 为 696 行，约 40 个状态与目录加载、轮询、Prompt 编辑、重画、发送和 JSX 混在同一组件。拆分后页面入口为 29 行，最大内部模块为 332 行；共享目录仍只加载一次，群默认风格与当天运行的状态边界明确分离。

## 契约保护

- `tests/test_v2_ui_router_contract.py` 固定 20 个 V2 UI 路由的路径、方法和 operation ID，并检查旧模块兼容导出。
- `frontend/src/pages/v2/ai-images/model.test.ts` 覆盖运行 key、404 可操作错误、Prompt 预览变量和时间展示。
- `frontend/e2e/ai-images.spec.ts` 通过全量拦截 `/api/**` 的 Fake API 验证目录、运行列表、详情与筛选；任何漏拦截请求都会直接失败。
- 既有归档、Prompt 编辑、损坏状态恢复测试继续覆盖移动后的后端实现。

## 当前结论

P2.3 完成的是低风险职责拆分，不是业务重写。两个 God Module 的修改半径已经缩小：以后修改归档查询不会同时碰 Prompt 编辑，修改群默认风格也不会同时进入运行级发送视图。`useAIImageRuns.ts` 仍集中管理同一运行工作区的状态机，暂不继续强拆，避免把相互依赖的状态分散成跨 Hook 隐式耦合。
