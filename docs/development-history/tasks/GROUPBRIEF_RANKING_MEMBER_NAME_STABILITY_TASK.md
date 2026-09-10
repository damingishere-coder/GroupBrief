# 排行榜成员名称稳定性修复任务

## 背景

- 茶馆两个不同成员被旧 WDA 错误解析为同一个“鲁布斯”，GroupBrief 随后生成了虚假的同名编号。
- Grok 的群主/邀请人字段 `c2341298` 被旧 WDA 当成大量成员的群名片。
- GroupBrief Provider 已有联系人回退，但群级名称策略会再次写回错误的上游名称。
- Eason 的名称数据完整，前端单行省略样式导致长群名片看起来不完整。

## 目标

- 最终名称采用“有效群名片原样优先，异常或缺失时回退联系人，最后稳定匿名名”。
- 统计身份始终以 `sender_id` 为准，只对最终仍真实同名的成员添加稳定编号。
- 排行榜相关页面完整换行显示成员名称。
- 同步修复 WDA 源码的 `chat_room.ext_buffer` 字段解析，但不替换正式安装。

## 允许修改范围

- GroupBrief 的联系人/群名片解析、WDA Provider 名称解析、群级名称策略及相关测试。
- 排行榜页、Dashboard 排行预览及对应样式。
- 隔离 WDA 工作树中的群名片解析函数和相关测试。

## 禁止修改范围

- 不修改排行榜模板、排名口径、数据库结构或 API/产物字段结构。
- 不刷新或改写 2026-08-28 及历史 `output` 产物。
- 不触发摘要、生图、微信发送、历史补跑或生产数据库迁移。
- 不替换或重启正式 WDA 2.2.1，不重启 GroupBrief 8766。
- 不修改 WDA 原工作区已有未提交内容，不重写 Git 历史或强制推送。

## 已确定实现要求

- `contact.db.chat_room.ext_buffer` 按字段 1=成员 ID、字段 2=群名片、字段 4=邀请人/群主解析。
- 一旦看到字段 1 的明确成员记录，不允许字段 4 兼容猜测覆盖该成员。
- GroupBrief 应直接读取群名片映射，使正式 WDA 尚未更新时后续任务也能正确解析。
- 联系人库明确返回的名称即使大小写与成员 ID 相同，也视为可信联系人名称。
- 群级策略保留 Provider 已解析的名称和来源，不再用 `upstream_sender_name` 覆盖。
- 长名称完整换行，并提供原始完整名称的 `title`。

## 验收标准

- 茶馆 `jiangzhema123` 显示“鲁布斯”，`to1900` 回退显示“罗斯”。
- Grok 不再将数十个不同成员统一显示为 `c2341298`。
- Eason 长群名片在排行榜详情与 Dashboard 预览中完整显示；数据库原值“广州”保持不变。
- 真同名仍稳定编号，消息数、发言人数和排名算法不变。
- 正式历史产物和运行服务零改动。

## 测试命令

- GroupBrief：`python -m pytest tests/test_contact_resolver.py tests/test_wechat_mcp.py tests/test_sender_name_policy.py -q`
- GroupBrief：`python -m pytest tests -q`（限定正式测试目录，排除 `data/audit-snapshot-*` 历史源码快照）
- 前端：`npm --prefix frontend test`、`npm --prefix frontend run build`
- Python 编译：`python -m compileall -q app tests`
- WDA 隔离工作树：`python -m pytest tests/test_group_nickname_ext_buffer_parsing.py -q`
- 两仓库：`git diff --check`

## 返回格式

- 报告根因、实际修改、针对性/全量测试结果、只读真实数据核验结果。
- 报告两个仓库的分支、提交哈希、远端地址和普通推送结果。
- 明确说明未刷新历史产物、未替换 WDA、未重启服务、未触发任何发送或生图。
