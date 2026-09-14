# Memory & Insight 分阶段实施

## 当前交付：Phase 0 消息来源与本地导入

此阶段新增可选的统一消息层、来源记录、后台导入账本和消息证据页面。
不改变日报流水线、图片、发送账本或原 output 文件。没有 AI 调用。

入口：运行任务 → 知识任务。先预览历史导入，再确认本地建库。
每条消息能查看上下文、来源归档及原记录；重复出现在多份快照中的消息只列一次。
无法确认所属群的归档保留为 orphan，可通过 `GET /api/v2/messages?include_orphans=true` 查看。

### 安装与回滚

默认 `KNOWLEDGE_ENABLED=false`。扩展表不通过应用启动自动创建。
先对指定数据库执行只读检查，再生成新副本：

```powershell
python scripts/migrate_knowledge.py --source data/groupbrief.db --dry-run
python scripts/migrate_knowledge.py --source data/groupbrief.db --output data/groupbrief-knowledge.db
```

脚本拒绝覆盖任何现有文件，保留 `user_version=1`，验证旧表内容 hash、记录数与外键。
副本旁保存迁移 manifest。正式切换必须在原服务停止写入后重新生成一致性副本，
确认无在途生成或发送后，将现有服务的 DATABASE_URL 指向该副本，再启动原服务。
不要在原服务继续产生新记录时切换到早先生成的旧副本。

配置 `KNOWLEDGE_ENABLED=true` 后，只有现有 FastAPI 调度 owner 启动子 worker。
不创建第二个监听端口或 Windows 计划任务。测试的 `GROUPBRIEF_NO_SCHEDULER=1` 同时禁用它。
该 worker 每五分钟扫描 `KNOWLEDGE_GROUP_IDS=1` 等明确灰度群的最近八个运行日期快照；
该配置默认为空，不自动扫描任何群。更早内容或其他群使用显式 Backfill。
当前阶段尚不从微信独立补采缺日。

回滚：关闭 KNOWLEDGE_ENABLED，恢复上一版代码和前端，保留扩展表及数据。
旧核心 schema 版本保持不变，不需要恢复整库，不覆盖升级后发送记录。

### 数据语义

- 标准化消息时间存 UTC；归档的本地闭区间转换为半开区间。
- 归档 hash 只证明内容一致；没有显式分页完整性证据时显示 unverified，不能当完整天。
- 有 ID 的旧消息标为 legacy_unknown；无 ID 的消息按捕获批次保留重复出现次数，标为 ambiguous。
- 同一消息 ID 对应不同正文等事实字段时保留首份正文及全部来源，标记 conflict，不覆盖原文。
- 完整批次提交后才进入查询；中断可按 250 条分批恢复。
- 来源/群归属变化使旧预览失效；任务过期后旧 worker 的写入被租约令牌拒绝。
- 新库不可用返回明确状态，不影响核心 readiness。后台任务不持有跨 AI 调用事务。

### API

`GET /api/v2/messages` 支持群、时间、用户、类型、删除群与孤儿筛选以及版本化游标。
详情、上下文和原来源分别为 `/messages/{id}`、`/context`、`/sources`。
`/knowledge/status`、`/knowledge/jobs` 提供状态，`/jobs/{id}/pause|retry` 控制本地任务。
`POST /knowledge/backfill/preview` 返回范围及版本；`POST /knowledge/backfill` 验证版本后返回 202。

## 后续阶段

1. Phase 1：完整消息覆盖、全员确定性 Weekly Insight、独立缺日读取和周期 revision。
2. Phase 2：中文 FTS5、跨日期检索、报告索引及性能验收。
3. Phase 3：AI 操作账本、分析复用、增量 Memory、证据验证、合并和撤销。
4. Phase 4：Storyline 时间轴和内容型周报。
5. Phase 5：Monthly Insight，复用消息与记忆底座。
6. Phase 6：存在实际召回缺口后再试点 Semantic Search。

各阶段独立 PR、检查、灰度和回滚。AI 未知结果不得自动重试；历史 AI 首批近 90 天仍需成本预览。
