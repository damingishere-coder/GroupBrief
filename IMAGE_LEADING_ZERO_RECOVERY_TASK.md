# 图片日期数字误判永久修复与 2026-09-04 恢复任务

## 背景

2026-09-01 的前导零修复仍停留在未合并的 PR #15，当前 `master` 与 Alter 托管的 8766 生产进程仍运行旧逻辑。2026-09-04 群 23（茶馆V3.0（三周年纪念）🐮🐴）再次失败：Level 3 本地信息图页眉包含运行日期 `2026-09-04`，严格 OCR 事实校验报“无证据数字：04”。简化 Prompt 没有携带本地信息图直接使用的 `run.json` 元数据，因此仅去除前导零仍不能覆盖 11 日以后等日期。用户要求修复本次失败并防止同类情况再次出现。

## 目标

- 将带前导零的整数 OCR 结果按其数值归一化，使 `05` 与 `5`、`09` 与 `9`、`01` 与 `1` 等价。
- 将 Level 3 渲染实际使用的可信 `run.json` 群名、运行日期、统计区间和计数加入事实证据，准确允许本次运行日期，同时继续拒绝其他日期和无依据数字。
- 图片恢复成功时清除对应的图片内容校验失败字段。
- 只恢复 2026-09-04 的群 23 图片，并保留 `send_hold=true` 与人工发送要求。

## 允许修改范围

- `app/image/fact_verification.py`
- `app/image/regeneration.py`
- `tests/test_image_fact_verification.py`
- `tests/test_v2_image_regeneration.py`
- 本任务说明文件
- 2026-09-04 群 23 的运行状态与图片产物（在备份后通过既有恢复入口写入）

## 禁止修改范围

- 不修改 Prompt、消息、排行榜或其他五个群的图片。
- 不调用微信发送、发送排程、邮件发送或目标核验接口。
- 不修改 Provider、登录、认证、`.env`、API Key 或远端权限。
- 不重启生产服务，不改写 Git 历史，不自动合并 PR。

## 已确定实现要求

- 只去除整数部分的冗余前导零，不放宽未知数字、单位、百分比和禁止事实校验。
- 恢复流程必须在图片进入 `READY_TO_SEND` 前写入并保留 `send_hold=true`、`needs_manual_send=true`。
- 群 23 仅使用已有 `run.json`、`ranking.json` 和已保存 Prompt 确定性重建 Level 3 本地信息图；禁止调用外部图片生成。
- 恢复结果必须通过 PNG 解码、尺寸、哈希及严格事实校验，写入前先备份原运行目录。

## 验收标准

- 前导零回归测试通过，未知数字仍然失败关闭。
- 图片内容校验失败经成功恢复后，`error`、`error_type`、`failed_stage` 被清除。
- 真实 `2026.09.04` OCR 页眉通过，伪造的其他运行日期仍失败关闭。
- 群 23 `daily_image.png` 存在、可解码、接口返回 `image/png`，状态为 `READY_TO_SEND`。
- 群 23 `send_hold=true`、`needs_manual_send=true`、`sent_at` 为空，且没有微信发送尝试证据。
- Dashboard 与 `run.json` 的图片成功/人工持有状态一致。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_image_fact_verification.py tests\test_v2_image_regeneration.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## 运行恢复记录

- 首次计划的恢复前目录复制因 PowerShell `-LiteralPath` 不展开通配符而失败，且该错误当时未终止后续恢复；因此不存在可声明为成功的恢复前文件副本。
- 恢复完成后已将 7 个产物逐文件 SHA-256 校验复制到 `output/.backups/image-date-fact-post-recovery-20260904-075918/`，用于保留当前验收态。
- 原失败证据仍保留在 `run.json` 的 `attempt_ledger`；恢复未调用外部图片生成或微信发送。

## 返回格式

- 根因与修复摘要
- 恢复图片的路径、尺寸、SHA-256、事实校验结果
- `send_hold` 与零发送证据
- Git 分支、提交、Push、PR、CI 状态与未合并说明
