# 图片前导零误判恢复任务

## 背景

2026-09-01 的群 26（米游涩泛二次元同好摸鱼群2.3）和群 27（米游涩泛二次元同好摸鱼群3.2）已生成图片，但严格 OCR 事实校验把版式或日期中的 `05`、`09`、`01` 当成未获证据支持的数字，最终进入图片失败状态。用户要求修复并补齐两张图片，但不得安排或触发微信图片发送。

## 目标

- 将带前导零的整数 OCR 结果按其数值归一化，使 `05` 与 `5`、`09` 与 `9`、`01` 与 `1` 等价。
- 图片恢复成功时清除对应的图片内容校验失败字段。
- 只恢复 2026-09-01 的群 26、27 图片，并保留 `send_hold=true` 与人工发送要求。

## 允许修改范围

- `app/image/fact_verification.py`
- `app/image/regeneration.py`
- `tests/test_image_fact_verification.py`
- `tests/test_v2_image_regeneration.py`
- 本任务说明文件
- 2026-09-01 群 26、27 的运行状态与图片产物（在备份后通过既有恢复入口写入）

## 禁止修改范围

- 不修改 Prompt、消息、排行榜或其他四个群的图片。
- 不调用微信发送、发送排程、邮件发送或目标核验接口。
- 不修改 Provider、登录、认证、`.env`、API Key 或远端权限。
- 不重启生产服务，不改写 Git 历史，不自动合并 PR。

## 已确定实现要求

- 只去除整数部分的冗余前导零，不放宽未知数字、单位、百分比和禁止事实校验。
- 恢复流程必须在图片进入 `READY_TO_SEND` 前写入并保留 `send_hold=true`、`needs_manual_send=true`。
- 群 26 仅可认领其任务记录中唯一且 SHA-256 匹配的候选图。
- 群 27 使用同一已保存 Prompt 重新生图；结果必须通过 PNG 解码、尺寸、哈希及严格事实校验。

## 验收标准

- 前导零回归测试通过，未知数字仍然失败关闭。
- 图片内容校验失败经成功恢复后，`error`、`error_type`、`failed_stage` 被清除。
- 两群 `daily_image.png` 均存在、可解码、接口返回 `image/png`，状态为 `READY_TO_SEND`。
- 两群 `send_hold=true`、`needs_manual_send=true`、`sent_at` 为空，且没有微信发送尝试证据。
- 调度快照与两个 `run.json` 的图片成功状态一致。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_image_fact_verification.py tests\test_v2_image_regeneration.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## 返回格式

- 根因与修复摘要
- 两张图片的路径、尺寸、SHA-256、事实校验结果
- `send_hold` 与零发送证据
- Git 分支、提交、Push、PR、CI 状态与未合并说明
