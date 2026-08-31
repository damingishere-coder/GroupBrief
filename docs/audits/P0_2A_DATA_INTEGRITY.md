# GroupBrief P0.2A 数据一致性取证

> 取证时间：2026-08-24 19:22（Asia/Shanghai）
>
> 数据基线：`data/groupbrief.db` 的 SQLite Online Backup 一致性快照
>
> 原则：只备份、只读聚合、只设计迁移；未修改正式数据库、Schema 或业务数据

## 1. 结论

正式数据库的物理结构当前完整，`PRAGMA integrity_check=ok`；问题属于**逻辑关系损失**，不是数据库文件损坏。

224 条 `group_runs` 中有 192 条（85.7%）引用已不存在的群 ID `1-22`。当前群 ID 只有 `23-29`。Git 历史证实旧版本曾对群执行物理删除，直到 2026-08-22 才改为软删除。因此，最可信解释是：旧群被物理删除并重新创建后，历史 `group_runs`/`reports` 被保留，但数据库没有外键阻止关系断裂。

目前不能安全自动修复旧群关系：`group_runs` 只保存本地整数 `group_id`，没有历史 `wechat_group_id` 快照；不能假设旧 ID `1` 对应新 ID `23`，也不能按顺序映射。

本轮决定：

- 保留全部 192 条历史记录。
- 不删除、不伪造群、不自动重关联。
- P0.2B 先建立“历史孤儿”正式表达，再加外键。
- 正式迁移前必须停止所有数据库写入者，并只在备份副本完成演练。

## 2. 一致性备份

使用 `sqlite3.Connection.backup()` 从只读源连接创建一致性快照；没有复制、移动或替换活动数据库。

备份文件：

```text
data/backups/groupbrief-p0-2a-consistent-20260824-192257.db
```

校验 Manifest：

```text
data/backups/groupbrief-p0-2a-consistent-20260824-192257.manifest.json
```

备份证据：

```text
字节数        1,052,672
SHA256        a17fca934b40d4c076e605df899c53a97082f415ea9a7e3699f725cba10fd6a4
integrity     ok
groups        7
runs          68
group_runs    224
reports       214
settings      55
provider      48
execution_log 0
```

源库与备份的四个核心表行数完全一致。备份目标的 `schema_version` cookie 从源库的 `25` 变为 `1`，这是 SQLite 内部 schema cookie，不是业务迁移版本，不能用于迁移判断。真正可用于版本判断的 `PRAGMA user_version` 在源库和备份中都为 `0`。

`PRAGMA foreign_key_check` 返回 0 行，但当前表根本没有声明外键，因此这不表示逻辑关系健康。

## 3. 已证实的数据问题

### 3.1 192 条孤儿 GroupRun

当前活动/软删除群 ID：

```text
23, 24, 25, 26, 27, 28, 29
```

孤儿记录引用的旧 ID 及数量：

```text
1:19   2:18   3:15   4:14   5:13   6:11
7:10   8:10   9:8   10:8  11:8   12:6
13:7  14:6   15:6   16:6  17:5   18:5
19:4  20:5   21:3   22:5
```

按报告日期：

```text
2026-08-13  166
2026-08-17   24
2026-08-18    2
```

最早日期为 2026-08-13，最晚日期为 2026-08-18。2026-08-13 的 166 条 `group_runs` 全部已经失去群关系，说明这是一批历史关系断裂，不是偶发单行错误。

### 3.2 孤儿 GroupRun 的内容完整程度

父 Run 状态：

```text
success 181
running  10
failed    1
```

阶段状态：

```text
ranking=success, prompt=success  164
ranking=success, prompt=skipped   22
ranking=success, prompt=pending    5
ranking=failed,  prompt=skipped    1
```

报告关系：

```text
有且仅有 1 条 Report  186
没有 Report             6
多条 Report             0
```

这意味着绝大多数孤儿仍包含可用历史结果。直接删除会丢失 186 条已有报告的归属上下文，不可接受。

### 3.3 五条“空成功”Run

以下父 Run 状态为 `success`，但没有任何 `GroupRun`：

```text
Run 32  success  auto    2026-08-17
Run 37  success  auto    2026-08-17
Run 60  success  manual  2026-08-18
Run 61  success  manual  2026-08-18
Run 65  success  auto    2026-08-18
```

它们不能证明核心业务成功，属于状态语义问题。当前没有足够日志确认是空群执行、旧流程写入还是中途清理；P0.2B 不应猜测性改成失败，先标记为待分类。

### 3.4 当前没有重复关系

以下聚合均为 0：

- 重复 `(run_id, group_id)` 关系
- 同一 `group_run_id` 对应多条 Report
- 非空 `wechat_group_id` 重复
- `group_runs` 缺失父 Run
- Report 缺失父 GroupRun

没有重复数据是好消息，但它完全依赖应用代码；数据库没有唯一约束阻止未来重复。

## 4. 根因置信度

### 已证实

- 初始版本到提交 `05b7da0` 之前，`repository.delete_group()` 使用 `session.delete(group)` 物理删除。
- 2026-08-22 才切换为当前软删除实现。
- 数据库没有 `group_runs.group_id → groups.id` 外键。
- 当前群 ID 从 23 开始，而全部孤儿只引用 1-22。
- 孤儿数据集中在旧群重新创建前后的 2026-08-13 至 2026-08-18。

### 高概率推断

旧群 ID `1-22` 曾存在，后来通过旧版本物理删除；群重新绑定/创建后获得 ID `23-29`。由于没有外键和稳定身份快照，旧 `group_runs`/`reports` 被保留成逻辑孤儿。

### 尚未证实

- 每个旧群 ID 对应哪个新群 ID。
- 五条空成功 Run 的具体形成动作。
- 10 条父 Run 为 `running` 的孤儿是否真的中断，还是旧流程没有收口。
- 是否能从历史 output 工件为每条孤儿恢复稳定微信群 ID；本轮没有读取业务内容。

## 5. Schema 与迁移问题

当前状态：

```text
PRAGMA foreign_keys = 0
PRAGMA user_version = 0
正式 migration version table = 不存在
```

缺少的关系保护：

- `group_runs.run_id → runs.id`
- `group_runs.group_id → groups.id`
- `reports.group_run_id → group_runs.id`
- `execution_logs.run_id → runs.id`

缺少的唯一/查询保护：

- 每个 GroupRun 最多一条 Report
- 非空 `wechat_group_id` 的唯一约束
- `runs(report_date, status)` 查询索引
- `execution_logs(run_id)` 查询索引

`UNIQUE(run_id, group_id)` 暂时不能直接添加。当前失败路径可能在同一个 Run/Group 上追加另一条失败记录；需要先统一 attempt/revision 语义，或者引入 `attempt_no`。

数据库列默认值也已经漂移：

```text
groups.summary_model  数据库默认 deepseek-v4-flash  / 代码默认 gpt-5.6-sol
groups.image_theme    数据库默认 blue_white        / 代码默认 random_preset
```

现有 `ALTER TABLE ADD COLUMN` 与 settings marker 不能表达列重建、约束升级和回滚，因此 P0.2B 需要正式 Schema 版本机制。

## 6. P0.2B 推荐设计

### 6.1 先建立历史身份表达

不要把外键直接套到当前非空 `group_id`。建议先将历史身份拆开：

```text
group_id          INTEGER NULL，引用当前 groups.id
legacy_group_id   INTEGER NULL，保存旧本地 ID
identity_state    TEXT NOT NULL，例如 active / legacy_orphan / unresolved
orphan_reason     TEXT NOT NULL，例如 legacy_group_hard_deleted
```

迁移规则：

- 当前能关联到 `groups` 的 32 条 GroupRun：保留 `group_id`。
- 192 条孤儿：将旧整数移入 `legacy_group_id`，`group_id=NULL`。
- 不创建假群，不做顺序映射。
- Reports 继续关联原 GroupRun，保留 186 条历史报告。

### 6.2 外键删除策略

建议历史数据优先：

- Group → GroupRun 使用 `ON DELETE RESTRICT`，禁止再次物理删除仍有历史的群。
- Run → GroupRun 使用 `ON DELETE RESTRICT`。
- GroupRun → Report 使用 `ON DELETE RESTRICT`。
- 业务删除继续使用现有 `deleted_at` 软删除。

不要使用级联删除历史报告。

### 6.3 正式迁移顺序

1. 停止 FastAPI scheduler、Windows 计划任务及所有数据库写入者。
2. 再创建一份停机前备份并验证 hash/integrity。
3. 在独立副本创建 Schema V1 迁移表和新表。
4. 按确定规则复制数据，不原地批量 UPDATE。
5. 建立外键、唯一约束和索引。
6. 开启 `PRAGMA foreign_keys=ON`，执行 `foreign_key_check` 和 `integrity_check`。
7. 用归档 API、历史 Report 数、Run 状态和完整测试验收。
8. 通过后才原子替换正式数据库。

### 6.4 回滚

- 保留原库和迁移后库，不在原库执行 down migration。
- 失败时停止所有写入者，用已验证备份原子恢复。
- 恢复迁移前代码提交。
- 再次核对完整性、核心表行数和 8766 健康。

## 7. 本轮明确未做

- 未修改正式数据库、Schema、索引或 PRAGMA。
- 未删除或重关联任何孤儿记录。
- 未读取聊天正文、Prompt、群名、发送目标或 Secret。
- 未停止/重启 8766 服务、调度器或计划任务。
- 未运行真实 AI、微信或邮件动作。

P0.2A 至此完成。下一轮只有在确认“历史孤儿建模方案”后，才进入 P0.2B 迁移实现与副本演练。
