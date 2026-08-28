# GroupBrief 六群排行榜与生图规则统一任务

## 背景

当前仅 Eason 群使用“文字主榜＋互动数”、WeChatDataAnalysis 名称和严格图片事实校验，其他五个活动群仍使用所有非系统消息参与排名及联系人解析名称。用户已确认六群统一，并要求从下一次任务起生效。

## 目标

1. 六个活动群统一采用文字消息决定名次、互动数仅展示的排行榜口径。
2. 排行榜名单下方固定显示：`说明：互动指图片、表情、引用等非文字消息，仅展示活跃度，不影响排名。`
3. 六群统一采用 WeChatDataAnalysis 的 `senderDisplayName`，并启用现有严格 Prompt 与 OCR 图片事实校验。
4. Dashboard、独立排行榜页面和发送用 `ranking.txt` 保持一致。

## 允许修改范围

- `templates/ranking/text_interactions.txt`
- Dashboard 与独立排行榜页面及其样式、测试
- 排行榜模板相关后端测试
- 本任务文件
- 生产 SQLite 中活动群 ID 23–27 的三个排行榜/名称配置字段

## 禁止修改范围

- 不重建或改写 2026-08-28 及更早的消息、排行榜、Prompt、图片和 `run.json`。
- 不调用生图、Prompt 重建、微信发送或补发接口。
- 不修改群发送时间、发送目标、图片主题、模型或其他群配置。
- 不修改已归档群或未来新建群的默认配置。

## 已确定实现要求

- `text_interactions` 模板在 `{{top_lines}}` 后只追加一次固定说明。
- 新口径页面显示 `文字 X｜互动 Y`；旧口径和历史 JSON 继续显示 `X 条`。
- Dashboard 与独立排行榜页面仅在 `count_policy=text_primary_with_interactions` 时显示说明。
- 生产数据库变更前创建备份；更新前重新核对 ID 23–27 的旧值，随后在一个事务内设置：
  - `ranking_count_policy=text_primary_with_interactions`
  - `ranking_template=text_interactions`
  - `sender_name_policy=wechat_data_analysis`
- ID 28 只核验，不重写；完成后六个活动群的三个字段必须一致。
- 现有 `uses_strict_image_fact_contract` 继续按排行榜口径启用，因此六群下一次生图统一执行严格事实校验。

## 验收标准

- 固定说明位于 Top 名单最后且只出现一次。
- 文字数决定名次；互动不参与排序；系统消息不计数。
- 新旧 `ranking.json` 均可被前端解析并正确展示。
- SQLite `integrity_check=ok`，`foreign_key_check` 无结果，六群配置一致。
- 2026-08-28 输出目录和发送状态未被改写；执行过程中没有发送或生图调用。
- 后端测试、编译检查、前端测试、生产构建和 `git diff --check` 全部通过。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m compileall -q app tests
Set-Location frontend
npm test -- --run
npm run build
Set-Location ..
git diff --check
```

## 返回格式

- 报告固定说明、六群最终配置、数据库校验、测试与构建结果。
- 报告今天/历史产物未重建且未发送。
- 报告 Git 提交哈希、分支、远端地址和普通推送结果。
