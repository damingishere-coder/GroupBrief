# GroupBrief 手动兜底生图｜Codex 提示词（旧自动化已停用）

> 当前主流水线会在每个群的 Prompt 完成后立即串行生图，不再需要固定时间运行本自动化。以下流程仅保留作人工兜底或故障恢复使用，不要配置周期调度。

需要人工兜底时，把下面整段内容作为一次性 Codex 任务提示词使用，并将工作目录设为你克隆的 GroupBrief 仓库根目录。

---

你正在执行 GroupBrief V2 的每日图片生成任务。必须遵守以下流程，不得改写流程、不得调用另一个 Codex CLI、不得使用外部图片 API。

## 目标

读取昨天报告归属日已经存在的 `image_prompt.txt`，使用 Codex 内置 `imagegen` 技能和内置 `image_gen` 工具逐群生成日报图片，将图片保存为：

`output/<安全群名>/<run_date>/daily_image.png`

任务中的 `report_date` 是排行榜和 Prompt 对应的统计归属日；`run_date` 是真实输出目录日期，也是后续 `begin`、`adopt`、`verify` 命令必须使用的日期。

成功后普通任务进入 `READY_TO_SEND`；重新生图回退任务进入 `ready_for_review` 并保持 `send_hold`，必须由用户审核确认。任何失败都必须如实报告，不能伪造成功。

## 强制规则

1. 工作区固定为当前 GroupBrief 仓库根目录。
2. 先完整阅读项目根目录 `AGENTS.md`（如果存在）和 `scripts/codex_image_automation.py` 的文件头说明。
3. 使用内置 `imagegen` 技能；每张图调用一次内置 `image_gen` 工具。不要执行 `codex exec`，不要调用 `scripts/image_gen.py`，不要要求 `OPENAI_API_KEY`。
4. 严格串行：上一群完成落盘和验证之后，才处理下一群。
5. 普通任务每群每天只生成一张；只有脚本返回 `regeneration: true` 时，才允许按脚本流程替换旧图，旧图会自动备份。
6. 不得修改 `messages.json`、`ranking.json`、`ranking.txt`、`image_prompt.txt`、数据库、群配置、`.env` 或任何密钥。
7. 不发送微信、不发送邮件、不执行 Git 提交或部署。
8. 最多处理脚本返回的 5 个任务；某一群失败时记录失败并继续下一群。

## 执行步骤

### 1. 查询昨天报告归属日待生图任务

在项目根目录运行：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py pending --limit 5
```

不带筛选参数时，脚本按应用时区查询昨天的报告归属日。如果 `count` 为 0，报告“昨天报告归属日没有待生图任务”并结束。不得自行创建 Prompt 或猜测日期。

### 2. 对每个任务依次执行

使用 `tasks` 数组中脚本返回的完整 `group_name` 和真实存储日期 `run_date`；不要把 `report_date` 当作输出目录日期。

第一步，在调用 ImageGen 前记录快照：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py begin --group "<完整群名>" --date "<run_date>"
```

只有 `ok: true` 时才继续。

第二步，完整读取 `begin` 返回的 `prompt_path` 文件。该文件内容是本次唯一生图要求，不得补写聊天中不存在的人物、事件、文字或数字。

第三步，调用 Codex 内置 `imagegen` 技能和内置 `image_gen` 工具生成一张全新图片：

- 这是新图生成，不使用参考图。
- 把 `image_prompt.txt` 的完整内容作为主要生成要求。
- 保留其中所有标题、数字、人物、场景、排版、配色和“禁止编造”约束。
- 不额外添加品牌、水印、二维码或不存在的聊天事件。

第四步，ImageGen 返回后认领本次新图片：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py adopt --group "<完整群名>" --date "<run_date>"
```

脚本会比较 `begin` 前后的 `$CODEX_HOME/generated_images`，只认领本次新增 PNG，并复制到正确的 `daily_image.png`。如果 Codex 明确返回了本地 PNG 路径、而自动扫描没有找到，可再执行：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py adopt --group "<完整群名>" --date "<run_date>" --source "<Codex 返回的 PNG 绝对路径>"
```

`--source` 只允许 `$CODEX_HOME/generated_images` 下的 PNG。

第五步，验证落盘和状态：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py verify --group "<完整群名>" --date "<run_date>"
```

必须同时满足：

- `ok: true`
- 输出文件名为 `daily_image.png`
- 文件非空且可识别为图片
- `run.json` 状态为 `READY_TO_SEND`（已经是 `SENT` 时保持 `SENT`）

否则该群算失败，不得报告成功。

### 3. 最终报告

用简洁表格报告：群名、日期、结果、最终状态、图片路径、失败原因。最后汇总成功数、失败数、跳过数。不要输出聊天原文、完整 Prompt、密钥或其他敏感配置。

---

需要补跑指定统计归属日时，只将查询命令改为：

```powershell
.venv\Scripts\python.exe scripts\codex_image_automation.py pending --report-date 2026-08-20 --limit 5
```

`pending --date 2026-08-21` 仍表示按真实运行目录日期过滤；它与 `--report-date`、`--all` 互斥。
