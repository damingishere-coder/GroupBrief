# GroupBrief P0.2B-1 离线迁移与副本演练

> 执行时间：2026-08-24（Asia/Shanghai）
>
> 范围：离线迁移工具、自动化测试、P0.2A 备份副本演练
>
> 未执行：正式数据库替换、ORM/API 切换、服务重启、真实外部调用

## 1. 结论

P0.2B-1 已达到“可以进入切换设计”的状态，但尚未部署到正式数据库。

显式离线迁移工具已经在 P0.2A 一致性备份上成功完成一次真实演练：192 条历史孤儿 `group_runs` 全部保留旧 ID，并从错误的活动关联转换为明确的 `orphaned` 状态；32 条仍能关联当前群的记录保持 `linked`；224 条 GroupRun、214 条 Report、68 条 Run 和 7 条 Group 总数均未变化。

迁移后副本的物理完整性、关系完整性和关键语义检查全部通过：

```text
integrity_check             ok
foreign_key_check_rows      0
linked_group_runs           32
orphaned_group_runs         192
preserved_legacy_group_ids  192
invalid_linked_group_ids    0
user_version                1
```

正式数据库 `data/groupbrief.db` 没有被迁移或替换，当前应用仍运行旧 Schema。

## 2. 交付内容

```text
app/db/offline_migrations.py  离线迁移、前置检查、事务重建和结果验证
scripts/migrate_db.py          薄 CLI 入口
tests/test_db_migration.py     迁移成功与失败边界测试
```

CLI 必须显式选择动作：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_db.py `
  --source <只读源数据库> `
  --output <必须不存在的新数据库> `
  --dry-run

.\.venv\Scripts\python.exe scripts\migrate_db.py `
  --source <只读源数据库> `
  --output <必须不存在的新数据库> `
  --apply
```

该模块没有接入 `app.main` 或 `repository.init_db()`，应用启动不会自动迁移。

## 3. 真实演练证据

演练源：

```text
data/backups/groupbrief-p0-2a-consistent-20260824-192257.db
SHA256 a17fca934b40d4c076e605df899c53a97082f415ea9a7e3699f725cba10fd6a4
```

最终演练输出：

```text
data/backups/groupbrief-p0-2b-rehearsal-final-20260824.db
SHA256 5990c894be7e05a073fb9b69447df41bb71b9d0be36fb31e3f97ae2e1ffa3e37
```

Manifest：

```text
data/backups/groupbrief-p0-2b-rehearsal-final-20260824.db.manifest.json
```

源文件迁移前后 SHA256 完全相同。演练输出和 Manifest 位于已被 Git 忽略的 `data/backups/`，不会进入仓库。

第一次演练产生的以下两个文件已被最终演练取代：

```text
data/backups/groupbrief-p0-2b-rehearsal-20260824.db
data/backups/groupbrief-p0-2b-rehearsal-20260824.db.manifest.json
```

本机安全策略阻止了自动删除，因此它们仍被保留为可恢复的旧演练产物；正式备份未受影响。

## 4. 迁移后的关系设计

### GroupRun 身份

仍可关联当前群：

```text
group_id        当前 groups.id
legacy_group_id NULL
identity_state  linked
orphan_reason   空字符串
```

历史孤儿：

```text
group_id        NULL
legacy_group_id 原旧本地 ID
identity_state  orphaned
orphan_reason   historical_group_missing
```

数据库 `CHECK` 约束禁止出现“显示 linked 但没有当前群”或“显示 orphaned 但丢失旧 ID”的矛盾状态。

### 外键与约束

- `group_runs.run_id → runs.id ON DELETE RESTRICT`
- `group_runs.group_id → groups.id ON DELETE RESTRICT`
- `reports.group_run_id → group_runs.id ON DELETE RESTRICT`
- `execution_logs.run_id → runs.id ON DELETE RESTRICT`
- 同一 GroupRun 最多一个 Report
- 非空、未删除群的 `wechat_group_id` 唯一

没有添加 `UNIQUE(run_id, group_id)`：当前强制重试/重新生成语义尚未正式建模，贸然添加可能阻断合法重试。

## 5. 安全边界和失败模式

- **输入输出相同：** 迁移拒绝，不打开写连接。
- **输出或 Manifest 已存在：** 拒绝覆盖，原文件保持不变。
- **WAL/SHM/Journal 侧文件存在：** 认为源库可能仍有写入者，迁移拒绝。
- **未知 `user_version`：** 拒绝覆盖其他迁移体系。
- **未知列、显式索引、触发器或依赖视图：** 拒绝静默丢失未来 Schema。
- **缺失父 Run、孤儿 Report、重复 Report、重复活动微信群 ID：** 前置检查失败，不创建输出。
- **表重建中断：** 事务回滚并删除本轮精确命名的临时文件。
- **迁移已执行：** 依据 `schema_migrations` 明确拒绝重复应用。
- **迁移后计数、外键或语义不一致：** 不提升临时数据库为正式输出。

迁移工具只使用 Python 标准库 `sqlite3`，没有新增第三方依赖。

## 6. Impact / Effort

```text
                LOW EFFORT                   HIGH EFFORT
           ┌────────────────────────┬──────────────────────────┐
HIGH       │ 已完成：离线迁移工具     │ 下一轮：ORM/API 正式切换   │
IMPACT     │ 已完成：副本演练和约束验证 │ 下一轮：停机迁移与恢复演练  │
           ├────────────────────────┼──────────────────────────┤
LOW        │ 可后续：CLI 文案美化      │ 暂缓：引入完整 Alembic 体系  │
IMPACT     │                        │ 暂缓：自动映射旧群到新群      │
           └────────────────────────┴──────────────────────────┘
```

## 7. 为什么本轮不修改 ORM/API

当前旧正式库没有 `legacy_group_id`、`identity_state` 和 `orphan_reason`。`SQLModel.metadata.create_all()` 不会给既有表增加这些列。

如果先更新 `models.py` 并重启应用，ORM 查询会直接报 `no such column`。因此必须把下一轮设计成一个有明确停机窗口的原子切换：

```text
停止所有写入者
  → 最终备份
  → 离线迁移新库
  → 更新 ORM/API 与连接级 foreign_keys=ON
  → 替换数据库
  → 启动并验证
  → 失败则停止服务并恢复原库/原代码
```

## 8. 下一轮 P0.2B-2 的切换条件

1. 明确 FastAPI scheduler、Windows Task 和其他写入者全部停止。
2. 迁移前确认没有 `-wal`、`-shm`、`-journal`。
3. 创建新的停机备份并记录 SHA256。
4. 同一个发布轮次更新 ORM、Run API、Dashboard 和邮件孤儿过滤。
5. SQLAlchemy 每个 SQLite 连接显式开启 `PRAGMA foreign_keys=ON`。
6. 定向测试、全量测试、旧库迁移测试和回滚测试全部通过。
7. 启动后只验证本地读取和健康状态，不触发 AI、微信或邮件。

在这些条件满足前，不应把演练数据库替换成正式数据库。

## 9. 本轮不在范围内

- 不修复 5 条“success 但无 GroupRun”的历史 Run。
- 不修复历史 `running` 状态。
- 不自动猜测旧群与当前群的对应关系。
- 不改 V1/V2 业务流程。
- 不引入 Alembic、微服务、事件总线或其他高复杂度方案。

## 10. 最终验证

当前最终工作树的独立验证结果：

```text
迁移定向测试      17 passed
完整 Python 测试  455 passed, 1 warning
Python 编译检查   passed
git diff --check  passed
8766 根页面        HTTP 200
```

唯一 warning 是现有 Starlette `TestClient` 对 `httpx` 调用方式的弃用提示，与本次迁移逻辑无关；应在依赖维护轮次处理，不值得阻塞本轮。

```text
╔══════════════════════════════════════════════╗
║ P0.2B-1 CODE OVERHAUL SUMMARY                ║
╠══════════════════════════════════════════════╣
║ Mode:              SURGICAL                  ║
║ Stack:             Python / SQLite           ║
║ Production DB:     unchanged                 ║
║ Orphans preserved: 192 / 192                 ║
║ Reports preserved: 214 / 214                 ║
║ Test failures:     0                         ║
║ Critical gaps:     runtime cutover pending   ║
║ Beads filed:       0                         ║
╚══════════════════════════════════════════════╝
```
