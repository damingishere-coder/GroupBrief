# 生图风格库与 Prompt 补强执行任务

## 背景

GroupBrief 已支持每日可复现随机风格、自定义风格、动态漫画分镜与运行级 Prompt 编辑，但随机风格家族尚未作为可搜索的正式目录开放，群级配置也只能在每日随机和自定义文本之间切换。现有 Prompt 对真实聊天事实已有约束，仍需进一步明确手机端画布、逐话题文字配额、可绘制镜头、空间不足时的降级顺序及重新生图不变量。

本任务只抽象借鉴外部风格分类与 OpenAI 官方图像提示词指南，不复制外部仓库的 Prompt、图片、品牌、角色、艺术家姓名或参考图依赖。

## 目标

- 把现有 10 个随机风格家族整理为带稳定键、分类、说明和色板的正式目录，并新增 12 个家族。
- 每个命名预设固定家族，但在家族内提供 16 种可复现的每日微变化；每日随机覆盖 22 个家族的 352 种组合。
- 群级默认风格和运行级风格共用紧凑、可搜索、可筛选且支持键盘操作的选择器。
- 强化微信手机端竖版画布、逐字文字、动作镜头、空间降级和重新生图不变量，不降低正常 5～7 个真实话题的信息密度。
- 修复 AI 图片页选题评分字段与后端不一致导致的潜在运行时错误。

## 允许修改范围

- `app/ai/image_themes.py` 风格目录、解析、兼容和公开元数据。
- V2 风格目录与解析 API 的响应元数据，不改变现有请求体。
- `templates/image_prompt/default.md`、`app/ai/prompt_templates.py` 与 `app/ai/prompt_builder.py` 的图片 Prompt 约束。
- `frontend/src/pages/v2/AIImages.tsx`、必要的前端类型和 `frontend/src/styles.css`。
- 与风格、Prompt、群 API、运行级编辑和 UI API 直接相关的测试。
- 本任务说明文件。

## 禁止修改范围

- 不新增或迁移数据库列，不改变调度、归档、微信发送、邮件发送或生图调用链路。
- 不读取真实聊天，不修改 `data/groupbrief.db`，不写入 `output/`。
- 不调用真实 ImageGen，不发送微信或邮件，不修改真实群配置。
- 不引入外部图片、预览图、品牌、角色 IP、艺术家姓名、`REFERENCE_0` 或参考图依赖。
- 不读取、输出或提交 `.env`、API Key、Token、Cookie、浏览器数据或其他 secrets。
- 不修改 Codex 模型提供商、登录方式或认证配置。

## 已确定实现要求

1. 公开目录固定包含 `random_preset`、`custom` 两个模式和 22 个命名预设；每项提供稳定键、名称、说明、类型、分类、三色色板和变化数量。
2. 每个家族提供两组画材、配色、纹理、光影候选，共 16 种组合；统一安全尾句只控制美术语言和视觉质感，不得删改事实、人物、数字或指定文字。
3. 目录版本升级为 `daily-style-v3`。种子包含版本、主题键、群键和运行日期；同群同日一致，跨日尽量避免与上一签名完全相同。
4. 安全的 `daily-style-v2` 已保存 `theme_prompt` 原样复用；旧版中夹带版式结构的 Prompt 拒绝复用。历史具体主题键继续解析但不在公开目录展示。
5. 群级保存命名预设时写入预设键并清空自定义文本；未保存草稿跨目标群切换保留。运行级切换只替换 Prompt 的【大主题】段。
6. Prompt 明确微信手机端 `1024×1536` 竖版、安全边距、逐话题短标题/真实姓名/事实句/主气泡、可绘制镜头、逐字文字恰好一次、空间降级顺序和重新生图不变量。
7. 前端评分字段使用后端真实的 `comedy`、`group_recognition`、`visual`、`discussion`、`participation`、`continuity`。

## 验收标准

- 后端目录测试覆盖 22 个公开家族、每个 16 种变化、唯一键、合法色板及禁用词/IP/参考图依赖。
- 确定性和兼容测试覆盖同日一致、跨日微变化、上一签名排除、v2 复用、旧版污染拒绝与历史主题键解析。
- API 测试覆盖稳定顺序、新字段、群配置预设保存及运行级只替换主题段。
- Prompt 测试确认文件模板与内置模板一致，新画布、文字配额、镜头动作、降级顺序和不变量存在，2～7 个真实话题校验不回退。
- 前端构建、后端定向测试、完整测试、`git diff --check` 通过。
- 隔离数据库浏览器检查覆盖搜索、分类、预设/自定义/随机、保存重载、运行级替换、评分卡、Esc 和 1280×720 横向布局。
- 最终检查实际 diff、依赖、硬编码、TODO/debug、临时文件和范围外修改。

## 测试命令

```powershell
$env:DATABASE_URL='sqlite:///data/style-library-smoke.db'
.\.venv\Scripts\python.exe -m pytest -q tests/test_daily_random_theme.py tests/test_v2_group_prompt_api.py tests/test_v2_prompt_builder.py tests/test_image_layouts.py tests/test_v2_prompt_editing.py tests/test_ui_api.py
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run build
git diff --check
```

浏览器检查使用隔离数据库和本地测试服务；不得连接真实生产数据库或触发任何外部发送/生图动作。

## 返回格式

- 风格目录、选择器、Prompt 补强和兼容性说明。
- 修改文件、定向/全量测试、前端构建和隔离 UI 检查结果。
- 明确声明未执行真实生图、未读取真实聊天、未发送微信或邮件、未修改生产数据库。
- Git 分支、提交哈希、远端仓库地址和推送结果。
