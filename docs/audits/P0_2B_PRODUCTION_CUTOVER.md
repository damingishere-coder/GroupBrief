# GroupBrief P0.2B-2 正式数据库关系切换

> 执行时间：2026-08-24（Asia/Shanghai）
>
> 模式：SURGICAL
>
> 范围：ORM/API、连接级外键、测试隔离、正式 SQLite 离线迁移与切换

## 1. 结论

P0.2B-2 已完成正式切换。正式服务当前使用 P0.2B Schema，8766 已由原 Alter 登记进程恢复运行，Windows 计划任务已恢复原启用状态。

迁移没有猜测历史群映射，也没有丢弃历史记录：

```text
groups             7  → 7
runs              68  → 68
group_runs       224  → 224
reports          214  → 214
linked             -  → 32
orphaned         192  → 192
```

192 条已经失去现存群关系的历史 GroupRun 现在使用 `identity_state=orphaned`、`group_id=NULL` 和 `legacy_group_id=<旧 ID>` 明确表达。32 条仍能关联当前群的记录保持 `linked`。

## 2. 正式切换证据

旧正式库在所有写入者停止后保持稳定：

```text
旧库路径       data/groupbrief.db
旧库 SHA256    a677eaa7e2654a1aee07f6a5e8712a1e73c228b3db0d1746ffd887fb3324bbd4
integrity      ok
user_version   0
```

正式迁移输出：

```text
迁移 Manifest  data/backups/groupbrief-p0-2b2-candidate-20260824-224331.json
新库 SHA256    6de4c332cf51b4313aeb52c5cdd4dd624bdeda656e78455133860445bef35f6a
integrity      ok
FK violations  0
user_version   1
migration_id   p0_2b_group_run_identity_v1
checksum       c14ecdc63408fa30cbfe02803098ddf9ffb2ce25fa3440d758f5e3a60accbb24
```

旧库已移动到精确回滚路径：

```text
data/backups/groupbrief-p0-2b2-original-20260824-224331.db
SHA256 a677eaa7e2654a1aee07f6a5e8712a1e73c228b3db0d1746ffd887fb3324bbd4
```

上述数据库与 Manifest 位于被 Git 忽略的 `data/backups/`，不会进入仓库。

## 3. 运行时改动

- ORM 增加 GroupRun 身份状态、历史群 ID、外键和一致性 `CHECK`。
- Report 与 GroupRun 建立 `RESTRICT` 外键及一对一唯一约束。
- ExecutionLog 与 Run 建立 `RESTRICT` 外键。
- 活动群 `wechat_group_id` 使用条件唯一索引；Run 增加日期/状态复合索引。
- SQLite 每个 SQLAlchemy 连接都执行 `PRAGMA foreign_keys=ON`。
- 启动时校验迁移记录、checksum、`user_version`、外键和关键索引；旧非空 Schema 会 fail closed，不会自动原地迁移。
- Run/Report API 显式返回历史孤儿身份；统计和邮件只使用 `linked` 记录。

## 4. 停写与原子替换

切换窗口内执行了以下保护：

1. 确认原 Alter 进程已停止、8766/8767 无监听。
2. 禁用 `GroupBriefDaily` 与 `GroupBriefDailySend`。
3. 确认没有 Uvicorn、日报生成或发送 Python 写入进程。
4. 确认没有 `groupbrief.db-wal`、`-shm`、`-journal`。
5. 对正式源库执行 dry-run 和 apply，迁移前后源 SHA256 相同。
6. 候选库先在 8767 启动并验证六个只读接口。
7. 同盘移动旧库到回滚路径，再移动候选库到正式路径；第二步失败时会立即移回旧库。
8. 启动 8766、验证数据库与接口后，恢复两个计划任务。

## 5. 启动后验收

正式库：

```text
integrity_check          ok
foreign_key_check rows   0
connection foreign_keys  1
user_version             1
groups/runs/gr/reports    7 / 68 / 224 / 214
linked/orphaned           32 / 192
```

只读 HTTP 验收：

```text
/                       200
/api/system/health      200
/api/system/status      200
/api/system/stats       200
/api/runs               200
/api/reports/latest     200
```

运行状态：

```text
Alter process ID   94507bc8-4b0e-4f37-89a2-1bffedb15fd3
Alter status       running
8766 listener      python/uvicorn child of the registered Alter process
GroupBriefDaily    enabled, Ready, next 2026-08-25 00:15
GroupBriefDailySend enabled, Ready, next 2026-08-25 08:30
```

没有执行真实 AI、微信或邮件发送。

## 6. 测试证据

主控与独立 Operator 最终结果一致：

```text
完整测试      458 passed, 1 warning
定向测试       80 passed, 1 warning
git diff check passed
8767 shadow    six read-only endpoints HTTP 200
8766 formal    six read-only endpoints HTTP 200
```

唯一 warning 是现有 Starlette `TestClient` 对 `httpx` 调用方式的弃用提示，与数据库切换无关。

测试数据库改为每个 pytest 进程独占的临时 SQLite 文件，避免并行测试、失败重跑和固定测试库残留互相污染。旧测试中依赖“无外键时可直接删父记录”的清场逻辑也已按依赖顺序修正。

## 7. 运行异常与处置

首次恢复服务时误用了 `alter start GroupBrief-Backend`。当前 Alter 版本把该参数解释为新脚本，因此短暂创建了一个同名空壳登记项，但它没有监听 8766，也没有访问正式数据库。

该空壳已按精确 ID 删除，并确认不存在。随后使用原登记 ID 执行 `alter restart`，原进程成功恢复；其他 Alter 项目没有被修改。

## 8. 回滚边界

P0.2B-2 的新代码会主动拒绝旧 Schema，因此回滚必须成对执行，不能只换回数据库：

1. 停止原 Alter 进程并禁用两个 GroupBrief 计划任务。
2. 确认无 8766/8767 监听、无 Python 写入者、无 SQLite sidecar。
3. 将代码恢复到本轮父提交 `df8966f58305a54b641e3301f648fdf445eba9eb`。
4. 将当前正式库移到新的故障留存路径。
5. 将 `groupbrief-p0-2b2-original-20260824-224331.db` 恢复为 `data/groupbrief.db`。
6. 启动并验证旧版本，再恢复计划任务。

不要在新代码下直接恢复旧库；Schema guard 会按设计拒绝启动。

## 9. 本轮未处理

- 不猜测 192 条历史孤儿对应哪个现存群。
- 不重写历史 Run 的 success/running 状态。
- 不添加 `UNIQUE(run_id, group_id)`，避免误伤现有强制重跑语义。
- 不重构 V1/V2 Pipeline、Provider 或调度架构。
- 不触发真实生成、发送或第三方服务。
