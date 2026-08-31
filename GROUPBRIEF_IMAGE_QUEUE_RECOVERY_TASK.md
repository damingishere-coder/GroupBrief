# GroupBrief 六群顺序生图恢复任务

## 背景

2026-08-28 的每日批次在 00:23 启动补偿后继续运行。用户要求 6 个启用群全部继续生图，并要求图片任务顺序执行。运行时已将 `image_generation_concurrency` 调整为 `1`，且调整发生在首个生图进程启动前。

群 24、27 在 Prompt 已成功保存后进入失败状态，错误为：

`Can't emit change event for attribute 'Group.image_enabled' - parent object of type <Group> has been garbage collected.`

六群随后均因同一错误进入等待重试，但失败状态的普通补写会不断重算 `next_retry_at`，导致到期时间持续后移，自动恢复无法真正开始。

## 目标

1. 修复群级运行覆盖配置复制后携带失效 SQLAlchemy 状态的问题。
2. 修复相同失败快照补写时不断推迟既有重试时间的问题。
3. 保持当日 Prompt、排行和消息快照可复用，不重复调用已经成功的阶段。
4. 让 6 个群按 Prompt 就绪顺序进入单并发生图队列。
5. 不触发微信发送。

## 允许修改范围

- `app/pipeline/daily_pipeline.py`
- `app/v2/reliability.py`
- 与该缺陷直接相关的自动化测试文件
- 本任务说明文件

## 禁止修改范围

- 微信发送、发送状态和发送目标逻辑
- Prompt 模板、选题、排行算法和图片内容规则
- 历史运行目录中的已有消息、排行、Prompt 和图片产物
- API Key、Token、密码、Cookie、`.env` 或浏览器数据

## 已确定实现要求

- 不再对从 SQLModel Session 加载的 `Group` 表对象直接使用会复制 `_sa_instance_state` 的 `model_copy()`。
- 运行覆盖值必须应用到具有独立 SQLAlchemy 状态的新 `Group` 实例。
- 保留覆盖字段白名单及结果顺序。
- 添加可复现“数据库对象被回收后写属性”的回归测试。
- 相同失败指纹的状态补写必须保留第一次计算出的 `next_retry_at`，不得无限推迟重试。
- 恢复时只使用现有幂等/重试机制，不重启第二批任务。

## 验收标准

- 回归测试在修复前可复现 `ObjectDereferencedError`，修复后通过。
- 相关并发与 V2 流水线测试通过。
- 今日失败群可从 `PROMPT_SAVED` 检查点继续，最终 6 个 `daily_image.png` 均有效落盘。
- 实际生图进程最大并发为 1。
- `sent=0`，无微信发送副作用。
- 数据库 `integrity_check=ok` 且 `foreign_key_check` 为空。

## 测试命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_generation_concurrency.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_reliability_state.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_v2_pipeline.py -q
.\.venv\Scripts\python.exe -m pytest tests -q
```

## 返回格式

- 根因与修复摘要
- 6 个群的最终图片路径、校验结果和实际生图顺序
- 测试结果
- Git 提交、分支和推送结果
- 明确说明未触发发送

## 2026-08-28 本轮执行结果

- 六个启用群均已进入单并发生图队列，实测同时存活的生图 Codex 进程最大为 1。
- 合格落盘：米游涩泛二次元同好摸鱼群1.1、米游涩泛二次元同好摸鱼群2.3、Eason张UED-4群🤘（本地 Pillow 兜底）。
- 人工保持：茶馆V3.0（三周年纪念）🐮🐴、Grok App 交流群的唯一候选均为有效 `864×1821` PNG，不符合固定 `1024×1536` 合同；米游涩泛二次元同好摸鱼群3.2 因网络断连且没有可信候选而结果未知。
- 三个失败项均未自动重试，也未裁切、拉伸或认领不合格候选；需用户针对本次失败明确授权后再决定重新生图或使用本地兜底。
- 微信发送计数为 0。
