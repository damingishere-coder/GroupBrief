# Memory & Insight 分阶段实施

## 当前交付：Phase 0 消息底座 / Phase 1 周度洞察 / Phase 2 本地搜索

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
Phase 1 可选独立缺日读取见下文；默认仍关闭。

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

1. Phase 1（已实现）：全员确定性 Weekly Insight、可选独立缺日读取和周期 revision。
2. Phase 2（已实现）：中文 FTS5、跨日期检索、报告索引及性能验收。
3. Phase 3：AI 操作账本、分析复用、增量 Memory、证据验证、合并和撤销。
4. Phase 4：Storyline 时间轴和内容型周报。
5. Phase 5：Monthly Insight，复用消息与记忆底座。
6. Phase 6：存在实际召回缺口后再试点 Semantic Search。

各阶段独立 PR、检查、灰度和回滚。AI 未知结果不得自动重试；历史 AI 首批近 90 天仍需成本预览。

## Phase 1：Weekly Insight

在现有“日报作品”内选择“Weekly · 周度洞察”。选择群及周期内日期即可提交计算任务；
可查看全部成员排行、发言天数、排名变化、新晋 Top、消息量变化、周冠军和每日趋势。
缺数据时显示已知消息数，隐藏不可靠的环比与冠军；未知日不画成 0。
身份不确定时展示人数范围，停止确定的个人排名变化推断。

新增 `report_insights`、`report_evidence` 两张扩展表。再次执行副本迁移脚本安装本阶段结构，
不修改核心版本。每期报告冻结规则、消息 ID / hash、基准期和覆盖记录；相同输入复用，
迟到数据产生新 revision，旧报告不变。来源入口使用该 revision 的消息集合。
统计全由程序计算，此阶段 AI 状态为 DISABLED，不生成或发送新图片。

### 独立缺日读取

配置 `KNOWLEDGE_CAPTURE_ENABLED=true` 和明确的 `KNOWLEDGE_SOURCE_SCOPE` 后才允许补采；
默认两个条件均不满足，不会访问微信。scope 表示本机账号与数据源范围，不是凭据。
必须确认历史与当前来源确属同一账号才能复用 `legacy:installation`，否则使用独立 scope；
不同 scope 不自动合并，混合来源周期不出正式环比。

灰度群每天 09:15 后检查最近七个自然日（包括周末）的覆盖缺口。优先使用已导入完整快照，
只补读缺口；每页最多等待 10 秒、一次完整取数最多 30 秒，避免长期占用微信读取服务。
读取前检查生成锁及现有发送 claim；主流程启动后不再提交下一页。结果保存到
`output/.knowledge/captures/<job_id>/<lease_token>/`，不覆盖日报归档；MCP 原始记录与来源一起保留。
只有明确的分页终止标记、有效结构和无源记录冲突才允许声明完整覆盖。

每日 10:00 后为空闲灰度群检查上一完整自然周。新的输入版本触发重算，相同结果复用。
该检查也能在周一停机后恢复。报告新旧版本始终只在工作台呈现，不进入现有自动发送链路。
月报仅有时间边界基础函数，尚未开放接口或调度；后续阶段另行交付内容结构。

### 验证记录

Phase 0：真实数据库副本导入 158 份可验证归档后得到 130,933 条去重消息，约 17 秒；
另外 6 份 hash 不一致归档被拒绝。原正式数据库和归档保持未修改。
Phase 1：增加跨日活跃人数、全员排行、零基数、数据缺口、身份不确定、迟到数据 revision、
闰年/跨年边界、分页完整性、主流程发送阻塞和 API 来源回溯测试。

## Phase 2：本地 FTS5 搜索

顶部“搜索群聊”或 Ctrl+K 打开搜索，继承能够确认的当前群筛选。
支持原始消息和群报文字两个对象范围，按群、日期、用户、相关度/时间筛选。
消息结果只列统一 Message，点击来源可以查看原归档与上下文。旧数据库 Report 及文件中的
ranking.txt、image_prompt.txt 作为历史文字检索，不凭此反推聊天事实，页面明确标注证据缺口。

中文本地单字/双字分词，英文和数字保留词元；NFKC 与大小写规范化，双引号用于连续短语。
FTS 只召回候选，命中后回到原文验证。高亮返回纯文本分段，前端不注入 HTML。
第一阶段没有 Embedding 或 RAG，也不承诺理解任意自然语言问句。

新增两组 Message/Report FTS 虚拟表和一个 `search_state` 技术水位表，均可重建，不是业务事实库。
增量处理新完成的来源批次及报告；每 250 条短事务提交并推进索引版本，旧分页游标失效时要求刷新。
全量重建写入另一组索引，完成后短事务切换；失败保留原可用索引。更新本地索引可以在搜索页提交；
重建 API 为 `POST /api/v2/knowledge/index`，正文 `{ "rebuild": true }`。
未知/未建立索引返回 503；查询超过限时返回 504 与 SEARCH_TIMEOUT，建议缩小筛选，不伪装成空结果。

FTS5 内置 rank 排序减少全量结果连接排序的开销，分页绑定索引及源数据版本。
实现参考 [SQLite FTS5 官方文档](https://sqlite.org/fts5.html)。
性能工具 `python scripts/benchmark_knowledge_search.py --counts 200000 1000000`
只生成临时合成数据库，运行完自动清理，不访问配置中的正式数据库。

迁移与回滚沿用前文副本流程。关闭 worker 后，已有索引仍能只读查询，但会显示水位落后；
回滚代码无需删除新增表，也不需要恢复整库。Memory 索引在后续 Memory 阶段增加。
