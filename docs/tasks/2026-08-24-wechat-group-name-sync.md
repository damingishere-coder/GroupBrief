# 微信群改名自动同步执行任务

## 背景

群聊会保留稳定的 `wechat_group_id`，但微信显示名称可能变化。当前系统只在人工绑定或人工修正时保存名称，自动发送依赖持久化的旧群名，导致群改名后需要 Codex 手工更正。群 ID 23 已证明稳定 ID 未变，而归档展示名仍为 V3、当前微信群名已变为 V4。

## 目标

- 按稳定微信群 ID 自动同步当前群名，不依赖名称相似匹配。
- 固定保留 `display_name` 作为归档名称，避免历史输出目录分裂。
- `send_target` 留空时自动跟随当前微信群名，非空时作为人工覆盖。
- 每日生成前和到期发送前同步名称，同时保留微信搜索与标题 OCR 复核。
- 提供无发送副作用的手工同步 API 和前端入口，并记录名称来源审计。

## 允许修改范围

- 群配置模型语义、数据库幂等迁移、群名同步服务及 API。
- V2 每日生成和到期发送流水线的名称同步接入与运行审计。
- 群聊列表、详情页和前端 API 类型。
- 对应后端/前端测试与本任务说明文件。

## 禁止修改范围

- 不改变 `wechat_group_id`、固定归档名、历史输出目录或既有归档归属。
- 不降低微信搜索候选限制、点击后标题 OCR 复核或重复发送保护。
- 不执行真实微信文字、图片、邮件或 AI 生图。
- 不读取、输出或提交 `.env`、API Key、Token、Cookie 或浏览器数据。
- 不修改 Codex 模型提供商、登录方式或认证配置。

## 已确定实现要求

1. 仅使用健康的真实 WeChatDataAnalysis 数据源快照，按 `wechat_group_id` 精确匹配当前名称；空名称、ID 伪名称和冲突名称不得写入。
2. 同步只更新 `wechat_group_name`；`display_name` 和稳定 ID 永不自动改变。
3. `send_target` 为空代表自动模式，实际目标按 `send_target or wechat_group_name or display_name` 计算；不同的非空值视为人工覆盖。
4. 一次性迁移只清空与旧 `wechat_group_name` 完全相同的重复目标，不覆盖不同的人工目标；运行迁移前使用 SQLite 在线备份保存生产数据库。
5. 每日生成开始前同步所有未删除群；到期发送批次开始前同步相关群并重新加载配置。同步不可用时按用户选择使用缓存名称，但必须保留现有微信标题复核。
6. `run.json`/日志记录 `fresh`、`cached` 或 `manual_override`、同步时间、当前微信群名和实际发送目标，且不得记录聊天正文或秘密。
7. 新增无发送副作用的同步 API；前端显示当前微信群名、固定归档名、自动/人工目标模式及手工同步按钮。

## 验收标准

- 测试覆盖改名、不变、人工覆盖、数据源不可用、ID 缺失、重复冲突、无效名称和缓存回退。
- 发送前成功同步后文字和图片都使用新名称；同步失败仍使用缓存名称并执行目标复核。
- 群 ID 23 保持 V3 固定归档名和原稳定 ID，当前微信群名/有效目标为 V4，原 V3 输出目录仍可读取。
- 后端定向测试、完整测试、前端构建和 `git diff --check` 通过。
- 真实运行仅调用同步与无副作用目标验证接口，不发送任何微信内容。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_group_name_sync.py tests/test_v2_group_migration.py tests/test_group_resolve.py tests/test_v2_pipeline.py tests/test_ui_api.py tests/test_v2_wechat_native.py tests/test_v2_wechat_sender.py tests/test_scheduler.py
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run build
git diff --check
```

## 返回格式

- 根因和自动/人工发送目标兼容性说明。
- 修改文件、迁移、定向/全量测试和前端构建结果。
- 生产数据库备份、群 ID 23、服务健康、无发送目标验证证据。
- Git 提交哈希、分支、仓库地址和推送结果。
