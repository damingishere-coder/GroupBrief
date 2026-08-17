# 群报 GroupBrief — V1 详细开发流程文档

> 版本：V1.0  
> 开发模型：DeepSeek V4 Flash  
> 运行环境：Windows 11 + PC 微信  
> 产品形态：本地 Web 小工具（浏览器访问 localhost）  
> 核心目标：从微信本地聊天记录中自动读取多个群聊，精确统计发言排行榜，并基于聊天内容生成可直接交给 GPT 生图的 Prompt，最终每天用一封邮件发送给用户。  
> V2 预留：Codex Automation 自动读取 Prompt → GPT 生图 → 自动打开微信 → 发送排行榜文字 + 海报图片。

---

# 0. 项目定位

## 0.1 产品名称

**中文名：群报**  
**英文名：GroupBrief**

GroupBrief 是一个运行在 Windows 本地的微信群聊日报工具。

V1 不做微信群实时监听，不依赖 WorkBuddy，不自动生图，也不自动向微信群发送消息。

V1 的核心链路是：

```text
Windows PC 微信
    ↓
WeChatDataAnalysis（主读取）
    ↓ 失败时
wechat-cli（备用读取）
    ↓
GroupBrief 本地整理
    ↓
精确统计排行榜
    +
DeepSeek V4 Flash 分析群聊并生成「GPT 生图 Prompt」
    ↓
按日期 / 群聊保存本地文件
    ↓
每天 09:00 发送一封纯文字邮件
    ↓
用户手动：
复制排行榜 → 微信
复制 Prompt → GPT 生图 → 微信
```

---

# 1. V1 最终交付内容

V1 每个群聊每天只生成两项最终内容：

## 1.1 发言排行榜文字

格式固定，参考现有微信群机器人效果。

示例：

```text
Eason张UED-4群🤘
消息统计
------------

时间起：2026-08-16 00:00:00
时间止：2026-08-16 23:59:59

------------

发言人数：48
总消息：864

------------

发言 Top10
1.广州【146】
2.将军的恩情-🖐🏻😭🖐🏻【71】
3.广州-产品-KONG【57】
4.阿寻（严厉抵制黄赌毒）【55】
5.🌸林诗雅小仙女【50】
6.深圳-牛马-空空【47】
7.XXX【33】
8.豆包本包【31】
9.北京-牛马-yuan【30】
10.福州-PM-Trent【29】
```

排行榜必须由程序做确定性统计。

**禁止让 LLM 计算排行榜数字。**

---

## 1.2 GPT 生图 Prompt

DeepSeek V4 Flash 阅读整理后的群聊内容，根据当天真实聊天事件生成一份：

> 可以直接复制到 GPT 图片生成能力中使用的最终 Prompt。

Prompt 不是普通聊天摘要，而是面向“漫画群聊日报海报”的视觉生成说明。

应包含：

- 群名称
- 统计日期 / 时间范围
- 总消息数
- 发言人数
- 当天主要事件
- 每个事件的背景
- 群内真实提及的人名
- 重要梗 / 代表性内容
- 对应漫画画面建议
- 海报标题
- 分区结构
- 配色
- 画面风格
- 底部总结文案
- 避免编造的要求

V1 不调用 GPT 图片生成。

V1 只负责生成 Prompt。

---

# 2. V1 不做的功能

以下功能明确不进入 V1：

- 微信实时监听
- WorkBuddy 读取聊天记录
- 自动调用 GPT 生图
- 自动将图片发送到微信
- 自动将排行榜发送到微信
- OCR
- 图片内容识别
- 语音 ASR
- 视频理解
- 企业微信
- 云端 SaaS
- 用户账号体系
- 多人权限管理
- 移动端 App

这些功能不得为了“完整”而提前开发。

---

# 3. V1 数据读取策略

## 3.1 主路线

优先使用：

**WeChatDataAnalysis**

目标：

直接读取 Windows PC 微信已经存在于本地的聊天记录。

GroupBrief 不关心 WeChatDataAnalysis 内部如何解析微信数据库，只关心它最终能否返回结构化消息。

---

## 3.2 备用路线

当 WeChatDataAnalysis：

- 不可用
- 启动失败
- 无法识别当前微信版本
- 无法读取目标群
- 返回数据明显异常

自动尝试：

**wechat-cli**

---

## 3.3 Provider 抽象

必须定义统一历史数据接口，不能把业务逻辑写死在某个开源工具里。

建议：

```python
class ChatHistoryProvider:
    def health_check(self) -> ProviderHealth:
        ...

    def list_groups(self) -> list[Group]:
        ...

    def fetch_messages(
        self,
        group_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[RawMessage]:
        ...
```

实现：

```text
WeChatDataAnalysisProvider
WechatCliProvider
```

未来允许加入：

```text
FutureProvider
```

业务层只能依赖：

```text
ChatHistoryProvider
```

不得直接依赖某个 GitHub 项目的内部数据库结构。

---

# 4. Provider 自动降级规则

一次群报任务中：

```text
WeChatDataAnalysis
    ↓
health_check
    ↓
可用？
 ┌───┴────┐
 是       否
 ↓         ↓
读取       wechat-cli
 ↓             ↓
有效？         读取
 ┌───┴───┐
 是      否
 ↓        ↓
继续     wechat-cli
```

Provider 需要返回明确状态：

```text
OK
UNAVAILABLE
UNSUPPORTED_WECHAT_VERSION
GROUP_NOT_FOUND
READ_FAILED
EMPTY_RESULT
INVALID_RESULT
```

不得静默失败。

---

# 5. 消息统一模型

无论底层使用哪个 Provider，都要转换成 GroupBrief 自己的统一消息模型。

建议：

```json
{
  "group_id": "",
  "group_name": "",
  "sender_id": "",
  "sender_name": "",
  "timestamp": "2026-08-17T14:21:36+08:00",
  "message_type": "text",
  "content": "",
  "source": "wechat_data_analysis",
  "source_message_id": "",
  "content_hash": ""
}
```

---

## 5.1 message_type

至少支持：

```text
text
image
emoji
voice
video
file
link
quote
red_packet
transfer
system
other
```

V1 不需要理解所有媒体内容。

但必须尽可能正确统计“这是由某个群成员发送的一条消息”。

---

# 6. 排行榜统计规则

以下规则确定为 V1 默认规则。

## 6.1 计入 1 条

以下用户消息默认都计入发言次数：

- 文字
- 图片
- 表情
- 语音
- 视频
- 文件
- 链接
- 引用 / 回复
- 红包（能够识别发送者时）
- 转账（能够识别发送者时）

---

## 6.2 不计入

- 系统通知
- 入群提醒
- 退群提醒
- 撤回提示
- 群名称变化
- 微信系统生成内容

---

## 6.3 连续消息

用户连续发：

```text
A
B
C
D
E
```

按：

**5 条消息**

计算。

不做“连续消息合并”。

---

## 6.4 最终指标

每个群每天至少输出：

```text
统计时间起
统计时间止
发言人数
总消息数
发言 Top10
```

---

# 7. 日期与调度规则

V1 自动执行时间：

## 7.1 08:45

开始：

- 获取聊天记录
- 清洗数据
- 统计排行榜
- 生成 DeepSeek 生图 Prompt
- 保存输出文件

---

## 7.2 09:00

发送邮件。

---

## 7.3 星期规则

### 周一

统计：

```text
周六 00:00:00
～
周日 23:59:59
```

属于“两天汇总”。

图片 Prompt 标题倾向：

```text
群里热闹这两天！
```

---

### 周二

统计：

```text
周一 00:00:00
～
周一 23:59:59
```

---

### 周三

统计：

```text
周二
```

---

### 周四

统计：

```text
周三
```

---

### 周五

统计：

```text
周四
```

---

### 周六

统计：

```text
周五
```

---

### 周日

**不生成，不发送邮件。**

---

# 8. 手动执行能力

V1 不能只依赖自动任务。

本地 UI 必须支持：

- 选择日期
- 选择群
- 单独生成一个群
- 一次生成所有启用群
- 重新生成
- 查看排行榜
- 编辑 Prompt
- 复制排行榜
- 复制 Prompt
- 手动发送邮件

这样即使自动流程失败，也可以手动恢复。

---

# 9. 多群设计

V1 实验阶段使用两个微信群，但系统不得写死为两个群。

前端设计：

```text
[群 A] [群 B] [+ 添加群聊]
```

每个群都是独立 Tab。

支持：

- 新增
- 删除
- 启用
- 停用
- 重命名显示名称
- 绑定微信真实群聊 ID / 名称

数据库中使用 `groups` 表管理。

---

# 10. 群配置字段

建议：

```text
id
display_name
wechat_group_id
wechat_group_name
enabled
provider_preference
created_at
updated_at
```

不要只依赖群名称作为唯一 ID。

如果底层能够获取稳定内部 ID，应优先保存内部 ID。

---

# 11. DeepSeek V4 Flash 的职责

DeepSeek V4 Flash 在 V1 中只负责：

> 根据整理好的群聊内容生成 GPT 生图 Prompt。

不负责：

- 读取微信数据库
- 统计排行榜
- 计算消息数量
- 判断发言人数
- 邮件发送
- 调度

---

# 12. AI Provider 抽象

虽然 V1 默认使用 DeepSeek V4 Flash，但不得把模型写死。

定义：

```python
class PromptGeneratorProvider:
    def generate_image_prompt(
        self,
        context: PromptContext
    ) -> ImagePromptResult:
        ...
```

实现：

```text
DeepSeekV4FlashProvider
```

未来可以扩展：

```text
OpenAIProvider
GeminiProvider
MiniMaxProvider
```

配置使用：

```env
AI_PROVIDER=deepseek
AI_BASE_URL=
AI_API_KEY=
AI_MODEL=
```

禁止把 API Key 写入 Git。

---

# 13. 发送给 DeepSeek 的聊天上下文

GroupBrief 不应该把数据库原始内容毫无处理地全部扔给模型。

先做本地整理。

流程：

```text
原始聊天
↓
过滤系统消息
↓
统一时间
↓
标准化发言人
↓
提取文本 / 链接标题 / 引用内容
↓
按时间顺序整理
↓
超长内容 Chunk
↓
DeepSeek 分段分析
↓
合并当天事件
↓
生成最终图片 Prompt
```

---

# 14. 超长群聊处理

当群聊消息很多时，不允许一次将全部消息提交给模型。

建议：

```text
Messages
↓
Chunk 1
Chunk 2
Chunk 3
...
↓
每块输出：
- 主要事件
- 重要人物
- 有趣内容
- 原话候选
↓
Merge
↓
最终 image_prompt.txt
```

模型上下文策略必须可配置：

```text
CHUNK_MESSAGE_COUNT=
MAX_CONTEXT_CHARS=
```

不要依赖固定模型上下文长度。

---

# 15. 生图 Prompt 规则

最终 Prompt 需要生成“可直接粘贴给 GPT 生图”的完整描述。

## 15.1 必须基于真实聊天内容

重点依据：

- 群里聊了什么
- 提到了谁
- 发生了什么事情
- 哪些梗最有意思

不是根据排行榜中的 Top10 发言人来决定海报人物。

---

## 15.2 可以保留真实提及的人名

如果聊天中自然提到：

```text
许总
小王
Ben
志明
```

可以出现在 Prompt 中。

重点是：

**聊天事件中的人物**

而不是：

**发言排行榜用户名。**

---

## 15.3 不得编造

DeepSeek Prompt 中必须加入约束：

- 不得创造聊天中不存在的事件
- 不得把两个人的行为混在一起
- 不确定的关系不要擅自下结论
- 不得凭空补充金额、时间、地点
- 原话引用必须来自真实聊天
- 可以幽默化标题，但不能改变事实

---

# 16. Prompt 输出结构建议

最终 `image_prompt.txt` 建议包含：

```text
【任务】
生成一张竖版微信群日报漫画信息图。

【群名称】
...

【统计时间】
...

【数据】
XX 条消息
XX 人发言

【主标题】
...

【副标题】
...

【整体视觉】
...

【版面 1】
标题：
事件：
代表人物：
建议画面：
可用文字：

【版面 2】
...

【版面 3】
...

【底部总结】
...

【硬性要求】
...
```

建议选取：

**5～8 个主要话题。**

具体数量由当天聊天内容决定。

---

# 17. 本地文件输出结构

V1 必须提前按照 V2 可接管的方式保存。

例如：

```text
GroupBrief/
│
├─ output/
│  └─ 2026-08-17/
│     │
│     ├─ group-a/
│     │  ├─ ranking.txt
│     │  ├─ image_prompt.txt
│     │  ├─ meta.json
│     │  └─ normalized_messages.json
│     │
│     └─ group-b/
│        ├─ ranking.txt
│        ├─ image_prompt.txt
│        ├─ meta.json
│        └─ normalized_messages.json
│
├─ data/
├─ logs/
└─ ...
```

真实文件夹名需做安全处理，避免：

- `/`
- `\`
- `:`
- `*`
- `?`

等 Windows 非法字符。

---

# 18. V2 Handoff 文件

为了让未来 Codex Automation 直接接管，V1 额外预留：

```text
handoff.json
```

建议结构：

```json
{
  "version": 1,
  "date": "2026-08-17",
  "group_id": "group-a",
  "group_name": "Eason张UED-4群🤘",
  "ranking_file": "ranking.txt",
  "prompt_file": "image_prompt.txt",
  "poster_file": null,
  "status": "prompt_ready"
}
```

V1：

```text
poster_file = null
```

V2：

Codex 生图后更新：

```text
poster_file = "poster.png"
status = "poster_ready"
```

然后发送微信。

---

# 19. 本地 Web UI

## 19.1 技术形态

本地 Web：

```text
http://127.0.0.1:8765
```

Windows 本地运行。

不开放公网。

---

## 19.2 UI 视觉

已确认：

**Apple 风格 + 蓝白配色 + 大气 + 简洁。**

设计原则：

- 大面积白色
- 轻微冷灰背景
- 低饱和蓝色作为主强调色
- 卡片边框极浅
- 圆角
- 大量留白
- 不做传统企业后台的密集表格
- 字体层级清晰
- 图标克制
- 不堆渐变
- 不做“科技大屏”

---

# 20. UI 页面结构

建议左侧导航：

```text
仪表盘
群聊管理
执行记录
文件管理
配置设置
日志
关于
```

---

# 21. 仪表盘顶部

显示：

- GroupBrief Logo / 名称
- 当前版本
- 服务状态
- 下次自动执行时间
- 立即执行
- 设置

---

# 22. 群聊 Tab

顶部：

```text
[群 A] [群 B] [群 C] [+ 添加群聊]
```

点击后切换当前群。

---

# 23. 群详情区域

一个群主要显示四块。

## A. 发言排行预览

显示最终：

```text
ranking.txt
```

支持：

- 复制
- 导出 txt

---

## B. 生图 Prompt

显示：

```text
image_prompt.txt
```

必须支持：

- 编辑
- 保存
- 复制
- 导出

因为用户需要人工检查 DeepSeek 是否正确理解群梗。

---

## C. 操作区

按钮：

- 生成群报
- 重新生成
- 复制排行榜
- 导出排行榜
- 复制 Prompt
- 导出 Prompt
- 预览邮件
- 手动发送邮件

---

## D. 海报预览区（V2 预留）

V1 显示空状态：

```text
海报预览
V2 即将支持
```

预留：

```text
poster_url
poster_file
poster_status
```

V2 Codex 生图之后，直接显示生成图片。

此区域 V1 不实现图片生成。

---

# 24. 仪表盘统计卡

建议只保留必要内容：

- 当前统计日期
- 已配置群数
- 总消息数
- 总发言人数
- 下一次执行时间

避免塞太多 KPI。

---

# 25. 系统状态

显示：

```text
WeChatDataAnalysis     可用 / 不可用
wechat-cli             可用 / 不可用
DeepSeek V4 Flash      可用 / 不可用
邮件服务               可用 / 不可用
```

用户一眼可以判断今天自动任务能否正常运行。

---

# 26. 群聊管理

支持：

```text
添加群
删除群
启用 / 停用
测试读取
查看最近数据
```

添加群时优先：

点击：

```text
添加群聊
```

然后从 Provider 读取到的群列表中选择。

避免用户手工输入错群名。

---

# 27. 配置页面

至少配置：

## 微信数据

```text
主 Provider：WeChatDataAnalysis
备用 Provider：wechat-cli
```

---

## DeepSeek

```text
API Base URL
API Key
Model
Timeout
Retry
```

Key 不允许完整回显。

---

## 邮件

```text
收件地址
发送方式
发件配置
```

---

## 自动任务

```text
生成时间：08:45
邮件时间：09:00
时区：Asia/Shanghai
```

---

# 28. 邮件设计

每天只发：

**一封邮件。**

不按群拆成多封。

---

## 28.1 邮件主题

建议：

```text
群报 GroupBrief｜2026-08-17
```

周一：

```text
群报 GroupBrief｜周末汇总｜2026-08-15～2026-08-16
```

---

## 28.2 正文

每个群一个区块。

例如：

```text
===== 群 A =====

【发言排行榜】

<ranking.txt>

【GPT 生图 Prompt】

<image_prompt.txt>


===== 群 B =====

【发言排行榜】

<ranking.txt>

【GPT 生图 Prompt】

<image_prompt.txt>
```

---

## 28.3 邮件中不出现

不要额外加入：

- AI 总结
- 解释
- 分析报告
- 今日洞察
- 产品提示
- 系统日志

邮件就是交付结果。

---

# 29. 邮件发送条件

08:45 开始生成。

09:00 发送。

发送前检查每个启用群：

```text
ranking.txt 存在？
image_prompt.txt 存在？
任务状态成功？
```

不得发送空白结果。

如果某个群失败：

- 不伪造数据
- 保留错误日志
- UI 显示失败
- 成功群仍保留结果

邮件发送策略建议做成配置项：

```text
SEND_PARTIAL_REPORT=true
```

默认：

```text
true
```

这样一个群失败，不影响其他成功群交付。

---

# 30. 数据库

建议使用 SQLite。

V1 不需要 PostgreSQL。

---

# 31. SQLite 表

至少：

```text
groups
runs
group_runs
reports
settings
provider_health
```

可选：

```text
message_cache
```

---

# 32. groups

```text
id
display_name
wechat_group_id
wechat_group_name
enabled
created_at
updated_at
```

---

# 33. runs

代表一次整体自动任务：

```text
id
report_date
range_start
range_end
trigger_type
started_at
finished_at
status
```

`trigger_type`：

```text
auto
manual
```

---

# 34. group_runs

每个群独立状态：

```text
id
run_id
group_id
provider_used
message_count
speaker_count
ranking_status
prompt_status
error_message
```

---

# 35. reports

保存：

```text
id
group_run_id
ranking_text
prompt_text
ranking_file
prompt_file
poster_file
email_status
created_at
updated_at
```

`poster_file`：

V1 为空。

V2 使用。

---

# 36. 推荐项目结构

```text
GroupBrief/
│
├─ app/
│  ├─ main.py
│  │
│  ├─ api/
│  │  ├─ groups.py
│  │  ├─ reports.py
│  │  ├─ settings.py
│  │  └─ runs.py
│  │
│  ├─ providers/
│  │  ├─ history/
│  │  │  ├─ base.py
│  │  │  ├─ wechat_data_analysis.py
│  │  │  └─ wechat_cli.py
│  │  │
│  │  └─ ai/
│  │     ├─ base.py
│  │     └─ deepseek_v4_flash.py
│  │
│  ├─ services/
│  │  ├─ history_service.py
│  │  ├─ message_normalizer.py
│  │  ├─ ranking_service.py
│  │  ├─ prompt_service.py
│  │  ├─ report_service.py
│  │  ├─ email_service.py
│  │  └─ handoff_service.py
│  │
│  ├─ scheduler/
│  │  ├─ calendar_rules.py
│  │  ├─ generate_job.py
│  │  └─ email_job.py
│  │
│  ├─ db/
│  │  ├─ models.py
│  │  ├─ repository.py
│  │  └─ migrations.py
│  │
│  └─ config/
│     └─ settings.py
│
├─ frontend/
│  ├─ src/
│  └─ ...
│
├─ data/
├─ output/
├─ logs/
├─ tests/
│
├─ .env.example
├─ .gitignore
├─ README.md
└─ start_windows.bat
```

---

# 37. 技术栈建议

## 后端

优先：

```text
Python
FastAPI
SQLite
SQLAlchemy / SQLModel
APScheduler
Pydantic
```

---

## 前端

保持轻量：

```text
React
Vite
TypeScript
```

UI 不建议安装过于庞大的企业组件库。

可以使用：

- 原生 CSS
- Tailwind
- 少量 Headless 组件

重点是控制 Apple 风格。

---

# 38. DeepSeek V4 Flash 开发原则

由于整个项目由 DeepSeek V4 Flash 协助开发，必须降低一次性任务复杂度。

不要一次要求：

> 把 GroupBrief 全部做完。

必须拆轮。

每轮：

1. 明确目标
2. 明确不做什么
3. 写代码
4. 测试
5. 汇报
6. 用户确认
7. 再进入下一轮

---

# 39. 开发轮次总览

建议 V1 共分：

```text
P0   项目骨架
P1   本地聊天读取 Provider
P2   消息标准化与排行榜
P3   多群与日期规则
P4   DeepSeek Prompt Generator
P5   本地输出与 V2 Handoff
P6   邮件
P7   自动调度
P8   本地 Web UI
P9   稳定性与验收
```

---

# 40. P0 — 项目骨架

## 目标

建立项目基础，不接微信。

完成：

- Python 后端
- SQLite
- 基础配置
- 日志
- FastAPI
- React/Vite
- localhost 页面
- `.env`
- `.gitignore`

---

## 验收

运行：

```text
start_windows.bat
```

打开：

```text
http://127.0.0.1:8765
```

能看到 GroupBrief 空仪表盘。

---

# 41. P1 — 微信历史读取

## 目标

实现：

```text
WeChatDataAnalysisProvider
```

并实现：

```text
WechatCliProvider
```

作为 fallback。

---

## 验收

本地微信已经有聊天记录。

程序可以：

- 列出群聊
- 选择群聊
- 指定日期
- 返回消息
- 返回发言人
- 返回时间
- 返回内容

---

## 本轮不做

- 排行榜
- DeepSeek
- 邮件
- UI 美化

---

# 42. P2 — 消息标准化 + 精确排行榜

完成：

```text
RawMessage
↓
NormalizedMessage
↓
RankingEngine
```

---

## 验收

人工抽样对比微信记录。

必须确认：

```text
消息总数
发言人数
Top10
```

统计逻辑正确。

---

# 43. P3 — 多群 + 日历规则

实现：

- 群增删
- 群启停
- 多群批量执行
- 周一 / 周二～周六 / 周日规则

---

## 验收

模拟一周：

```text
周一 → 周末两天
周二 → 周一
...
周六 → 周五
周日 → 不运行
```

全部正确。

---

# 44. P4 — DeepSeek V4 Flash Prompt Generator

实现：

```text
NormalizedMessages
↓
Chunk
↓
DeepSeek
↓
Topic Merge
↓
image_prompt.txt
```

---

## 验收

选择真实群聊一天数据。

检查生成 Prompt：

- 是否抓到真正的话题
- 是否没有编造
- 是否保留重要人名
- 是否能直接交给 GPT 生图
- 是否包含画面建议

---

# 45. P5 — 本地输出 + V2 Handoff

生成：

```text
ranking.txt
image_prompt.txt
meta.json
normalized_messages.json
handoff.json
```

按：

```text
日期 / 群
```

隔离。

---

## 验收

两个群不会串文件。

重复生成不会覆盖错误日期。

---

# 46. P6 — 邮件

实现每天一封。

邮件内容：

```text
群 A
Ranking
Prompt

群 B
Ranking
Prompt
```

---

## 验收

先发送测试邮件。

确认：

- 中文正常
- emoji 正常
- 排行格式保持
- Prompt 未被邮件格式破坏

---

# 47. P7 — Scheduler

08:45：

```text
GenerateDailyReports
```

09:00：

```text
SendDailyEmail
```

周日不执行。

---

## 验收

将时间改到测试时间。

验证：

```text
自动读取
自动生成
自动保存
自动发邮件
```

---

# 48. P8 — 本地 Web UI

依据已确认设计实现。

重点：

- Apple 风格
- 蓝白
- 简洁
- 多群 Tab
- Ranking
- Prompt
- 操作区
- V2 海报预览位
- 状态
- 历史执行记录
- 设置

---

# 49. P9 — 稳定性

补充：

- Provider fallback
- API timeout
- DeepSeek retry
- 邮件 retry
- 任务防重复
- 日志
- 错误状态
- 手动重跑
- 文件安全
- 单元测试
- README
- Windows 启动脚本

---

# 50. 防重复执行

同一：

```text
date + group + range_start + range_end
```

必须生成唯一任务标识。

自动任务已经成功时，不重复生成。

手动点击重新生成允许：

```text
force=true
```

---

# 51. 日志

至少：

```text
logs/app.log
logs/provider.log
logs/ai.log
logs/scheduler.log
logs/email.log
```

不得将：

- API Key
- 邮件密码
- 完整敏感认证信息

写日志。

---

# 52. 隐私

聊天记录属于本地敏感数据。

原则：

```text
微信聊天
↓
只在本机读取
```

只有生成 Prompt 所需文本才提交给 DeepSeek API。

不得：

- 上传原始数据库
- 自动上传整个聊天库
- 将 output 提交 Git
- 在日志完整打印聊天

---

# 53. .gitignore

至少：

```gitignore
.env
data/
output/
logs/
*.db
*.sqlite
*.sqlite3
__pycache__/
node_modules/
dist/
```

---

# 54. V2 预留接口

V1 不实现，但代码结构必须留接口。

## 54.1 ImageGenerationProvider

```python
class ImageGenerationProvider:
    def generate(
        self,
        prompt_file: Path
    ) -> GeneratedImage:
        raise NotImplementedError
```

V2 预计由：

```text
Codex Automation
↓
读取 image_prompt.txt
↓
调用 GPT 图片生成
↓
poster.png
```

---

## 54.2 WeChatDeliveryProvider

```python
class WeChatDeliveryProvider:
    def send_text(self, group, text):
        raise NotImplementedError

    def send_image(self, group, image_path):
        raise NotImplementedError
```

V1 不提供实现。

---

## 54.3 UI 海报窗口

V1 保留：

```text
poster_file
poster_status
```

V2 图片生成后直接展示。

---

# 55. V2 预计流程

```text
GroupBrief 08:45
↓
读取群聊
↓
ranking.txt
image_prompt.txt
handoff.json
↓
Codex Automation
↓
读取 handoff
↓
GPT 自动生图
↓
poster.png
↓
Codex 打开微信
↓
选择目标群
↓
发送 ranking.txt
↓
发送 poster.png
↓
更新 handoff
↓
status = sent
```

---

# 56. V2 与 V1 的核心边界

V1：

```text
GroupBrief
↓
邮件
↓
人
↓
GPT
↓
微信
```

V2：

```text
GroupBrief
↓
Codex Automation
↓
GPT
↓
微信
```

因此 V1 的文件输出格式和 Provider 抽象一定要提前规范。

---

# 57. V1 最终验收标准

V1 只有全部满足以下条件才能结束：

## 数据

- 能读取当前 Windows 微信聊天记录
- WeChatDataAnalysis 可作为主 Provider
- wechat-cli 可以 fallback
- 能正确区分不同群
- 能正确按时间范围过滤

## 排行榜

- 发言人数正确
- 总消息数正确
- Top10 正确
- 格式与目标截图基本一致

## Prompt

- DeepSeek 能正确分析真实聊天
- 不明显编造
- Prompt 可以直接用于 GPT 生图

## 多群

- 至少两个真实群测试
- 可继续新增群
- 群之间数据不串

## UI

- localhost
- Apple 蓝白风
- 群 Tab
- 排行榜
- Prompt 编辑
- V2 海报预览占位
- 执行记录
- 设置

## 自动化

- 08:45 自动生成
- 09:00 自动邮件
- 周日不执行
- 周一正确统计周六+周日

## 手动

- 可以指定日期
- 可以指定群
- 可以重新生成
- 可以手动发邮件

---

# 58. DeepSeek V4 Flash 每轮开发纪律

每轮开发结束必须输出：

```text
1. 本轮做了什么
2. 新增 / 修改文件
3. 核心实现逻辑
4. 如何启动
5. 如何测试
6. 自动测试结果
7. 人工验收步骤
8. 已知问题
9. 下一轮建议
```

没有用户明确确认：

**不得自动进入下一轮。**

---

# 59. 第一优先级

整个项目最重要的不是 UI，也不是 DeepSeek。

第一优先级始终是：

> **微信本地聊天记录读取是否准确。**

顺序必须坚持：

```text
数据可靠
↓
统计可靠
↓
AI Prompt
↓
邮件
↓
自动化
↓
UI 完善
```

不能反过来。

---

# 60. V1 产品成功标准

如果 V1 跑通，用户每天只需要做：

```text
09:00
收到 GroupBrief 邮件
↓
复制群 A 排行榜
↓
复制群 A Prompt → GPT 生图
↓
复制群 B 排行榜
↓
复制群 B Prompt → GPT 生图
↓
手动发到对应微信群
```

其余：

- 聊天记录读取
- 时间范围判断
- 排行榜计算
- 群聊内容整理
- Prompt 生成
- 文件归档
- 邮件发送

全部由 GroupBrief 自动完成。

这就是 V1 的最终边界。

---

# 61. V1 → V2 的升级原则

V2 不重做 V1。

只替换最后一段：

```text
V1：
ranking + prompt
↓
email

V2：
ranking + prompt
↓
Codex Automation
↓
GPT 图片
↓
微信
```

因此 V1 开发时必须确保：

```text
ranking.txt
image_prompt.txt
handoff.json
```

是稳定、清晰、机器可读的交接协议。

---

# 62. 最终开发顺序

严格执行：

```text
P0 项目骨架
↓
P1 WeChatDataAnalysis + wechat-cli
↓
P2 精确排行榜
↓
P3 多群 / 日期规则
↓
P4 DeepSeek V4 Flash Prompt
↓
P5 文件与 V2 Handoff
↓
P6 邮件
↓
P7 自动任务
↓
P8 Apple 风格 Web UI
↓
P9 稳定性 / 验收
```

不要并行大规模开发。

先保证每层是可验证的，再进入下一层。

---

**项目名称：群报 GroupBrief**  
**V1 核心：本地读取 → 精确排行 → DeepSeek 生图 Prompt → 邮件**  
**V2 核心：Codex Automation → GPT 生图 → 微信自动发送**
