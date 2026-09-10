# GroupBrief 微信群聊搜索验证修复任务

## 背景

2026-08-30 08:30 六个群的文字与图片均已准备完成，但微信搜索浮层中的小字号“最常使用”标题未被 Windows OCR 识别。现有发送器因“可信分区 0”按设计停止，六群都在提交前失败，没有产生重复发送。

## 目标

- 保留现有 OCR 分区选择与点击后标题复核。
- 当分区标题 OCR 缺失时，仅允许唯一且完整匹配 `search_item_<群名>` 的 Windows UI Automation 群聊搜索项作为兜底。
- 修复后先无副作用验证六个目标，再按用户本次明确授权补发 2026-08-30 六群文字与图片。

## 允许修改范围

- `app/sender/wechat_native.py`
- `tests/test_v2_wechat_native.py`
- `requirements.txt`
- 本任务文件

## 禁止修改范围

- AI Provider、认证、`.env` 和模型配置。
- 群名、群 ID、排行榜文案、Prompt 和已生成图片。
- 与本故障无关的 CodeMap、前端和调度器逻辑。
- 任何 `SEND_RESULT_UNKNOWN` 或已提交任务的自动解锁/重发。

## 已确定实现要求

- UIA 候选必须与微信主窗口属于同一进程。
- Automation ID 必须严格等于 `search_item_<完整目标群名>`，文本标题也必须匹配，且候选中心必须位于搜索浮层范围内。
- 匹配数不是 1 时必须失败关闭；禁止选择聊天记录或搜索网络结果。
- UIA 点击后仍必须通过当前聊天标题 OCR 二次校验。
- 依赖不可用或 UIA 歧义时不得降级为猜测点击。

## 验收标准

- 新增 UIA 唯一匹配、歧义、越界和 OCR 失败兜底测试通过。
- `tests/test_v2_wechat_native.py` 及相关后端测试通过。
- 实际 `POST /api/groups/{id}/verify-send-target` 对六群全部返回唯一目标。
- 补发前备份当天六份 `run.json` 和数据库，并用 CAS 解除明确未提交的 `failed_final` 锁。
- 补发后六群均需要 `SENT`、`submitted=true`、`ui_observed`、`outcome_unknown=false`，且图片 SHA-256 与回执一致。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_v2_wechat_native.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

## 返回格式

- 根因与修复边界。
- 代码与依赖变更。
- 测试、线上目标验证、逐群发送证据。
- Git 分支、提交、Push、PR 与 CI 状态。
