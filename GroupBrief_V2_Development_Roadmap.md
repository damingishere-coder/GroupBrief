# GroupBrief V2 全自动群聊日报开发路线

> 版本：V2 开发路线草案  
> 日期：2026-08-18  
> 运行环境：Windows 长期开机、不锁屏  
> 核心目标：**全自动读取微信群聊天记录 → 自动统计排行榜 → AI 自动生成生图 Prompt → Codex 自动生图 → 自动保存归档 → 自动发送到指定微信群**

---

# 一、V2 最终目标

GroupBrief V2 不再只是“聊天记录整理工具”，而是升级为一套可以长期无人值守运行的 **微信群日报自动生成与发布系统**。

V2 每个启用的微信群，在指定统计周期内自动完成：

1. 从 **WeChatDataAnalysis** 读取指定群聊、指定时间段的聊天记录。
2. 程序自动统计：
   - 发言人数
   - 总消息数
   - 发言 Top10
3. 根据可编辑的排行榜模板生成微信可直接发送的排行榜文字。
4. 将聊天记录交给 **DeepSeek V4 Flash** 进行内容理解。
5. 根据可编辑的生图 Prompt 模板生成最终生图 Prompt。
6. 由 **Codex `$imagegen` / GPT Image 2** 串行生成日报图片。
7. 按照“群聊 / 日期”自动保存聊天数据、排行榜、Prompt、图片和运行状态。
8. 到每个群配置的发送时间后，通过 Windows 微信自动化发送：
   - 第一条：排行榜文字
   - 第二条：AI 日报图片
9. 任一阶段失败时记录日志，不发送明显不完整的结果。
10. 前端提供群管理、模板编辑、任务状态、历史日报等管理能力。

---

# 二、V2 明确不做的功能

为了避免 V2 范围继续膨胀，以下能力本轮明确不做：

- 不做微信消息实时监听。
- 不做 AI 实时回复群消息。
- 不做群聊机器人互动。
- 不发送“一句话日报”。
- 不做日报文字摘要的单独发送。
- 不做 Windows 锁屏状态下自动发送微信。
- 不做复杂的自动重试策略。
- 不做短信、邮件、企业微信等其他发送渠道。
- 不做多账号微信切换。
- 不做服务器部署，V2 继续运行在本地 Windows 电脑。
- 不把 GroupBrief 核心逻辑和某一个微信自动化项目强绑定。

---

# 三、V2 最终发送内容

每个群每天只发送两条内容。

## 第一条：排行榜文字

排行榜格式必须支持 Unicode / Emoji，可直接复制粘贴到微信聊天框。

当前默认格式：

```text
===== 茶馆V3.0（三周年纪念）🐮🐴 =====

【发言排行榜】

茶馆V3.0（三周年纪念）🐮🐴
消息统计
------------

时间起：2026-08-17 00:00:00
时间止：2026-08-17 23:59:59

------------

发言人数：27

总消息：409

------------

发言 Top10
1.停用【94】
2.罗斯【78】
3.啊菌菌阿菌【53】
4.杯面大英雄【39】
5.一颗苹果【35】
6.春夏秋冬【18】
7.梓木【18】
8.大明同学【17】
9.吉米多的围棋【7】
10.神奇小郭【7】
```

此格式不得写死在 Python 代码里。

必须由“排行榜模板”控制，并允许在 V2 前端直接修改。

---

## 第二条：AI 日报图片

当前图片生成链路：

```text
群聊原始数据
    ↓
DeepSeek V4 Flash
    ↓
根据可编辑模板生成最终生图 Prompt
    ↓
image_prompt.txt
    ↓
Codex `$imagegen`
    ↓
GPT Image 2
    ↓
daily_image.png
```

每个群每个统计周期只生成 **1 张图片**。

图片必须一张一张串行生成，不允许 5 个群同时发起生图任务。

---

# 四、统计周期规则

默认统计规则：

| 生成日 | 统计范围 |
|---|---|
| 周一 | 周五 00:00:00 ～ 周日 23:59:59 |
| 周二 | 周一 00:00:00 ～ 23:59:59 |
| 周三 | 周二 00:00:00 ～ 23:59:59 |
| 周四 | 周三 00:00:00 ～ 23:59:59 |
| 周五 | 周四 00:00:00 ～ 23:59:59 |
| 周六 | 不生成 |
| 周日 | 不生成 |

默认每天：

```text
08:00 开始数据读取、统计、AI 总结和生图
```

发送时间不写死为统一时间。

每个群可以独立设置：

```text
send_time
```

例如：

```text
08:30
08:35
08:40
```

---

# 五、V2 总体架构

```text
                         GroupBrief V2
                               │
                               ▼
                     ┌──────────────────┐
                     │    Scheduler     │
                     │  每日 08:00 启动   │
                     └────────┬─────────┘
                              │
                              ▼
                     ┌──────────────────┐
                     │   Group Config   │
                     │ 读取启用的群与规则  │
                     └────────┬─────────┘
                              │
                              ▼
                ┌─────────────────────────────┐
                │     WeChatDataAnalysis      │
                │     本地 API / 数据接口       │
                └──────────────┬──────────────┘
                               │
                               ▼
                    指定群 + 指定时间段消息
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
        ┌──────────────────┐      ┌──────────────────┐
        │ Ranking Engine   │      │ DeepSeek V4 Flash│
        │ 代码确定性统计      │      │ 群聊内容理解        │
        └────────┬─────────┘      └────────┬─────────┘
                 │                         │
                 ▼                         ▼
           ranking.txt              image_prompt.txt
                 │                         │
                 │                         ▼
                 │               ┌──────────────────┐
                 │               │ Codex $imagegen  │
                 │               │   GPT Image 2    │
                 │               └────────┬─────────┘
                 │                        │
                 │                        ▼
                 │                 daily_image.png
                 │                        │
                 └────────────┬───────────┘
                              ▼
                    ┌──────────────────┐
                    │  WeChat Sender   │
                    │ Windows UI Auto  │
                    └────────┬─────────┘
                             ▼
                        指定微信群
```

---

# 六、技术路线

## 1. 聊天记录读取

数据源使用：

**LifeArchiveProject / WeChatDataAnalysis**

V2 不通过人工操作 WeChatDataAnalysis 前端导出数据。

优先使用它已经提供的本地 API 能力。

GroupBrief 需要新增独立适配层：

```text
WeChatDataSource
```

接口建议：

```python
class WeChatDataSource:
    def health_check(self):
        ...

    def resolve_group(self, group_name):
        ...

    def fetch_messages(self, group_id, start_time, end_time):
        ...
```

GroupBrief 业务层不直接依赖 WeChatDataAnalysis 内部数据库表。

---

## 2. 排行榜统计

排行榜必须由 Python / 代码完成，不使用 AI 计算。

至少统计：

```text
总消息数
发言人数
发言 Top10
```

统一返回结构化结果：

```json
{
  "group_name": "茶馆V3.0（三周年纪念）",
  "period_start": "2026-08-17 00:00:00",
  "period_end": "2026-08-17 23:59:59",
  "speaker_count": 27,
  "message_count": 409,
  "top_speakers": [
    {
      "rank": 1,
      "name": "停用",
      "count": 94
    }
  ]
}
```

再由排行榜模板渲染成最终 `ranking.txt`。

---

## 3. AI 总结与 Prompt 生成

模型：

```text
DeepSeek V4 Flash
```

V2 中 DeepSeek 主要负责：

- 阅读当天聊天内容。
- 提炼热点事件。
- 提取群聊里的梗、人物、争议、事件。
- 根据“生图 Prompt 模板”填写最终图片生成提示词。

注意：

**不要让 DeepSeek 计算排行榜。**

排行榜数字必须来自代码。

---

## 4. 图片生成

图片生成使用：

```text
Codex `$imagegen`
GPT Image 2
```

规则：

- 每群每天最多生成 1 张。
- 串行执行。
- 当前群生成成功后才能开始下一群。
- 最终图片必须自动保存到该群该日期目录。
- 不允许生成后只存在 Codex 临时界面里。
- 必须在 P4 单独完成“自动落盘”验收。

---

## 5. 微信自动发送

V2 暂定使用：

```text
wechat-automation-api
```

通过 Windows UI Automation 控制已登录的 PC 微信。

GroupBrief 自己新增：

```text
WechatSender
```

抽象接口：

```python
class WechatSender:
    def health_check(self):
        ...

    def send_text(self, target, text):
        ...

    def send_image(self, target, image_path):
        ...
```

当前 Provider：

```text
WechatAutomationSender
```

以后如果更换为其他微信方案，只替换 Provider。

---

# 七、运行环境要求

V2 本地运行条件：

- Windows 10 / 11
- 电脑长期开机
- 关闭自动休眠
- 关闭自动锁屏
- 微信 PC 客户端保持登录
- WeChatDataAnalysis 可正常读取当前微信账号数据
- GroupBrief 后台服务保持运行
- Codex / ChatGPT Desktop 保持可用于 Scheduled Task 和 `$imagegen`
- DeepSeek API Key 配置正确

---

# 八、建议目录结构

```text
GroupBrief/
│
├─ app/
│  ├─ data_sources/
│  │  └─ wechat_data_analysis.py
│  │
│  ├─ ranking/
│  │  ├─ engine.py
│  │  └─ renderer.py
│  │
│  ├─ ai/
│  │  ├─ deepseek_client.py
│  │  └─ prompt_builder.py
│  │
│  ├─ image/
│  │  └─ image_task.py
│  │
│  ├─ sender/
│  │  ├─ base.py
│  │  └─ wechat_automation.py
│  │
│  ├─ scheduler/
│  │  ├─ period.py
│  │  └─ scheduler.py
│  │
│  └─ pipeline/
│     └─ daily_pipeline.py
│
├─ config/
│  ├─ groups.yaml
│  └─ app.yaml
│
├─ templates/
│  ├─ ranking/
│  │  └─ default.txt
│  └─ image_prompt/
│     └─ default.md
│
├─ output/
│  ├─ 茶馆V3.0（三周年纪念）/
│  │  └─ 2026-08-18/
│  │     ├─ messages.json
│  │     ├─ ranking.json
│  │     ├─ ranking.txt
│  │     ├─ image_prompt.txt
│  │     ├─ daily_image.png
│  │     └─ run.json
│  │
│  └─ ...
│
├─ logs/
│  └─ 2026-08-18.log
│
├─ frontend/
│
└─ scripts/
   ├─ run_daily_pipeline.py
   ├─ test_wechat_data.py
   ├─ test_image_generation.py
   └─ test_wechat_send.py
```

---

# 九、群配置结构

每个群至少支持以下字段：

```yaml
groups:
  - id: tea_house
    name: "茶馆V3.0（三周年纪念）"

    enabled: true

    schedule_rule: "weekday_default"

    send_time: "08:30"

    summary_model: "deepseek-v4-flash"

    prompt_model: "deepseek-v4-flash"

    image_enabled: true

    send_target: "茶馆V3.0（三周年纪念）"

    ranking_template: "default"

    image_prompt_template: "default"
```

V2 前端至少能编辑：

- 群名称
- 是否启用
- 统计周期规则
- 发送时间
- 总结模型
- Prompt 模型
- 是否生图
- 发送目标
- 排行榜模板
- 生图 Prompt 模板

---

# 十、任务状态设计

每个群每次运行生成一个 `run.json`。

推荐状态：

```text
PENDING
DATA_READY
RANKING_READY
PROMPT_READY
IMAGE_READY
READY_TO_SEND
SENT
FAILED
```

示例：

```json
{
  "group_id": "tea_house",
  "run_date": "2026-08-18",
  "period_start": "2026-08-17 00:00:00",
  "period_end": "2026-08-17 23:59:59",
  "status": "IMAGE_READY",
  "failed_stage": null,
  "error": null,
  "updated_at": "2026-08-18 08:12:03"
}
```

---

# 十一、失败日志

V2 暂时不做复杂自动重试。

任一步失败：

```text
记录日志
↓
标记 FAILED
↓
停止该群后续步骤
↓
继续处理其他群
```

错误类型建议：

```text
WECHAT_DATA_UNAVAILABLE
GROUP_NOT_FOUND
MESSAGE_FETCH_FAILED
RANKING_FAILED
DEEPSEEK_FAILED
PROMPT_FAILED
IMAGE_GENERATION_FAILED
IMAGE_FILE_MISSING
WECHAT_OFFLINE
SEND_TEXT_FAILED
SEND_IMAGE_FAILED
```

---

# 十二、开发轮次

---

# P0｜V2 基线固化与工程整理

## 给 Codex 的话（可复制）

```text
你现在要在现有 GroupBrief V1 项目上开始 V2 开发。

本轮只做 V2 基线整理，不开发完整自动化功能。

请先完整阅读当前项目代码、README、配置、现有页面、现有消息统计逻辑、现有 DeepSeek 调用逻辑和现有生图 Prompt 生成逻辑。

V2 产品边界如下：

1. V2 不做实时监听微信群。
2. V2 不发送一句话日报。
3. 每个群最终只发送两项：
   - 发言排行榜文字
   - AI 日报图片
4. 聊天数据以后从 WeChatDataAnalysis 本地接口自动获取。
5. 发言排行榜由代码确定性统计，不交给 AI。
6. 消息总结和生图 Prompt 生成使用 DeepSeek V4 Flash。
7. 图片生成由 Codex `$imagegen` / GPT Image 2 完成。
8. 图片每个群每天只生成一张，并且严格串行生成。
9. 所有结果按照“群聊 / 日期”进行文件夹归档。
10. 排行榜格式必须模板化并支持前端编辑。
11. 生图 Prompt 模板必须支持前端编辑。
12. 微信发送通过独立 WechatSender Adapter 完成，当前目标 Provider 为 Windows 微信自动化。
13. 每个群可独立配置：
    - 群名称
    - 是否启用
    - 统计周期规则
    - 发送时间
    - 总结模型
    - Prompt 模型
    - 是否生图
    - 发送目标
14. 默认统计规则：
    - 周一统计周五+周六+周日
    - 周二统计周一
    - 周三统计周二
    - 周四统计周三
    - 周五统计周四
    - 周六、周日不生成
15. 默认 08:00 开始处理，但发送时间由群独立配置。
16. 本地运行环境为 Windows 长期开机、不休眠、不锁屏。

本轮任务：

A. 审计现有 V1 结构。
B. 列出 V1 中可以直接复用的模块。
C. 列出 V2 必须新增或重构的模块。
D. 不破坏现有 V1 可运行能力。
E. 创建 V2 架构文档。
F. 创建必要的目录骨架，但不要提前写大量业务实现。
G. 为后续 P1-P8 预留清晰接口。
H. 所有改动 Git 化，提交一个独立 commit。

本轮禁止：
- 不要接 WeChatDataAnalysis。
- 不要接微信发送。
- 不要真正调用 `$imagegen`。
- 不要大改前端。
- 不要删除现有 V1 能力。

完成后输出：
1. 改动文件列表
2. V1 可复用模块
3. V2 新模块
4. 风险点
5. 下一轮 P1 应该做什么
6. commit hash
```

## 为什么现在做

V2 涉及数据读取、AI、Codex 生图、微信发送和前端大改。

如果直接开始写自动化脚本，很容易继续把 V1 逻辑耦合在一起。

所以第一轮先让 Codex 真正理解现有代码，再建立 V2 的接口边界。

## 做完之后得到什么

得到一个：

```text
V1 还能继续运行
+
V2 已经有清晰骨架
+
后续每轮可以独立开发
```

的稳定基础。

## 验收标准

- V1 现有流程仍能运行。
- 已创建 V2 架构文档。
- 已建立 Adapter / Pipeline 等基础目录。
- 无真实外部发送行为。
- Git 工作区干净。
- 有独立 P0 commit。

---

# P1｜WeChatDataAnalysis 数据接入

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P1：WeChatDataAnalysis 数据源接入。

目标：
让 GroupBrief 不再依赖人工导入聊天记录，而是能够根据“群 + 时间范围”自动从本机 WeChatDataAnalysis 获取聊天消息。

WeChatDataAnalysis 项目：
https://github.com/LifeArchiveProject/WeChatDataAnalysis

请优先使用它现有的本地 API / MCP / chat export 能力，不要重新实现微信数据库解密，不要直接耦合其内部数据库表。

本轮要求：

1. 新建 WeChatDataSource 抽象。
2. 实现 WeChatDataAnalysisSource。
3. 支持：
   - health_check
   - 获取/解析群聊
   - 根据群标识获取指定时间段消息
4. 优先使用 source=auto。
5. 允许配置 WeChatDataAnalysis 地址，默认本机：
   http://127.0.0.1:10392
6. 增加测试脚本：
   scripts/test_wechat_data.py
7. 测试时只读取数据，禁止修改微信数据。
8. 将获取的数据统一规范化成 GroupBrief 自己的 Message Schema。
9. 保存测试结果到项目 output/test-data/。
10. 不要在业务代码中直接散落 WeChatDataAnalysis API 路径。
11. 任何失败必须输出明确错误类型。
12. 所有改动 Git 化并独立 commit。

GroupBrief Message Schema 至少包含：

- message_id
- group_id
- group_name
- sender_id
- sender_name
- timestamp
- message_type
- content

如果原始数据还有其他字段，可以保留在 raw 字段中。

本轮不做：
- 排行榜
- DeepSeek
- 图片
- 自动发送
- 前端大改

完成后输出：
1. 实际使用了 WeChatDataAnalysis 哪个接口
2. 获取一段测试群聊的结果
3. 数据字段映射关系
4. 异常情况
5. commit hash
```

## 为什么现在做

V2 的所有能力都依赖“能稳定自动获取聊天记录”。

这是最底层数据入口，必须优先打通。

## 做完之后得到什么

GroupBrief 可以做到：

```text
指定群
+
指定开始时间
+
指定结束时间
↓
自动得到标准化聊天数据
```

以后不需要手动导出聊天记录。

## 验收标准

- WeChatDataAnalysis 正常时可获取测试群聊天。
- 时间筛选正确。
- 群映射正确。
- Emoji / 中文昵称不乱码。
- 数据失败有明确日志。
- 不修改源数据库。

---

# P2｜统计周期引擎 + 排行榜统计

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P2：统计周期与排行榜引擎。

目标：
根据运行日期和群配置自动计算统计周期，并从聊天数据中生成确定性的发言排行榜。

默认规则：

周一：
周五 00:00:00 → 周日 23:59:59

周二：
周一 00:00:00 → 23:59:59

周三：
周二 00:00:00 → 23:59:59

周四：
周三 00:00:00 → 23:59:59

周五：
周四 00:00:00 → 23:59:59

周六：
不生成

周日：
不生成

本轮要求：

1. 创建独立 PeriodResolver。
2. 不允许在各业务脚本里重复写 weekday if/else。
3. 为统计周期编写单元测试。
4. 创建 RankingEngine。
5. 统计：
   - 总消息数
   - 发言人数
   - 发言 Top10
6. 排行榜排序规则必须确定性。
7. 同数量时定义稳定排序规则。
8. 不使用 AI 计算任何排行榜数字。
9. 输出 ranking.json。
10. 本轮先生成一个最简 ranking.txt，模板化留到 P3。
11. 过滤系统消息等规则以现有 V1 逻辑为基础，先保持兼容。
12. 所有改动独立 commit。

本轮不做：
- DeepSeek
- 生图
- 微信发送
- 前端

完成后输出：
1. PeriodResolver 规则
2. 排行榜数据结构
3. 测试结果
4. 一份真实测试输出
5. commit hash
```

## 为什么现在做

排行榜是最终群里一定会发送的内容，而且它必须准确。

所以必须先把 AI 完全排除在统计链路之外。

## 做完之后得到什么

任意一个群和日期，都可以自动得到：

```text
统计时间范围
总消息数
发言人数
Top10
```

## 验收标准

- 周一正确统计三天。
- 周六周日正确跳过。
- Top10 数量正确。
- 中文、Emoji 昵称正常。
- 同一次输入输出完全一致。

---

# P3｜排行榜模板系统

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P3：排行榜模板系统。

目标：
让排行榜文案完全从代码中抽离，用户以后可以修改格式而不改 Python。

默认模板参考当前版本：

===== {{group_name}}🐮🐴 =====

【发言排行榜】

{{group_name}}🐮🐴
消息统计
------------

时间起：{{period_start}}
时间止：{{period_end}}

------------

发言人数：{{speaker_count}}

总消息：{{message_count}}

------------

发言 Top10
{{top10_lines}}

本轮要求：

1. 创建 templates/ranking/。
2. 实现 RankingRenderer。
3. 支持模板变量。
4. 支持 UTF-8 和 Emoji。
5. 输出 ranking.txt。
6. 模板格式错误时不能让整个服务崩溃，要给出明确错误。
7. 支持恢复默认模板。
8. 每个群允许选择 ranking_template。
9. 为模板渲染写测试。
10. 不要在本轮大改前端，但先提供后端模板 CRUD 接口供后续前端使用。
11. 所有改动独立 commit。

本轮不做：
- DeepSeek
- 生图
- 微信发送
- 前端视觉重构

完成后输出：
1. 支持的模板变量
2. 默认模板文件
3. 示例渲染结果
4. API 说明
5. commit hash
```

## 为什么现在做

排行榜样式你已经明确还会继续调整。

现在就做模板化，可以避免之后每改一个 Emoji 都要找 Codex 改代码。

## 做完之后得到什么

你可以只修改：

```text
templates/ranking/default.txt
```

就改变所有排行榜文案格式。

---

# P4｜DeepSeek V4 Flash 生图 Prompt 流水线

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P4：DeepSeek V4 Flash 生图 Prompt 流水线。

目标：
自动读取指定统计周期的聊天记录，通过 DeepSeek V4 Flash 生成最终可供 GPT Image 2 使用的生图 Prompt。

本轮不负责真正生成图片。

要求：

1. 复用现有 V1 DeepSeek 调用能力。
2. 固定默认模型为 DeepSeek V4 Flash。
3. 不纳入 DeepSeek V4 Pro。
4. 创建 ImagePromptBuilder。
5. 创建 templates/image_prompt/。
6. 当前默认模板保留现有 V1 的结构，包括：
   - 任务
   - 群名称
   - 统计时间
   - 数据
   - 主标题
   - 副标题
   - 整体视觉
   - 各个版面
7. 模板必须可编辑。
8. 每个群允许选择不同 image_prompt_template。
9. DeepSeek 输入必须包含：
   - 标准化聊天内容
   - 群名
   - 统计周期
   - 消息数
   - 发言人数
10. 输出：
    image_prompt.txt
11. 同时保存模型调用的结构化元数据，不保存 API Key。
12. Prompt 生成失败时标记 PROMPT_FAILED。
13. 对超长聊天记录做稳定的分块/压缩策略，避免简单暴力截断导致重要内容丢失。
14. 所有改动独立 commit。

本轮不做：
- `$imagegen`
- 图片发送
- 前端大改

完成后输出：
1. DeepSeek 输入结构
2. Prompt 模板结构
3. 一份测试 image_prompt.txt
4. 超长聊天处理方案
5. commit hash
```

## 为什么现在做

图片质量取决于 Prompt。

而且你后面还会和 Codex继续调这个 Prompt，所以必须把它做成长期可编辑资产。

## 做完之后得到什么

每天每个群都能自动得到：

```text
image_prompt.txt
```

而且不用人工复制聊天记录给 AI。

---

# P5｜Codex `$imagegen` 串行自动生图与落盘

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P5：Codex `$imagegen` 自动生图。

目标：
读取 P4 输出的 image_prompt.txt，使用 Codex `$imagegen` / GPT Image 2 自动生成图片，并把图片可靠保存到 GroupBrief 对应的群聊/日期目录。

这是 V2 的关键验证轮次，本轮优先保证真实闭环，不追求复杂抽象。

要求：

1. 读取待处理群的 image_prompt.txt。
2. 使用 `$imagegen`。
3. 每个群每天只生成 1 张。
4. 严格串行：
   - 当前群生成成功并确认文件存在
   - 才允许开始下一个群
5. 保存路径必须固定为：
   output/<群名称>/<日期>/daily_image.png
6. 不允许图片只存在 Codex 临时上下文或 UI 中。
7. 生成后验证：
   - 文件存在
   - 文件大小 > 0
   - 能被正常解析为图片
8. 成功状态 IMAGE_READY。
9. 失败状态 IMAGE_GENERATION_FAILED。
10. 保留 image_prompt.txt。
11. 不重复生成已经 IMAGE_READY 的同一运行，除非显式 force。
12. 增加手动测试入口。
13. 验证 Windows 长期开机、桌面不锁屏情况下 Scheduled Task 可执行。
14. 本轮先不要自动发送微信。
15. 所有改动独立 commit。

特别注意：
如果 Codex `$imagegen` 无法稳定把生成图片保存到指定项目路径，请不要掩盖问题。
请记录实际行为、限制和替代实现，再决定是否需要引入其他落盘桥接方案。

完成后输出：
1. 实际 `$imagegen` 调用方式
2. 图片落盘路径
3. 串行队列实现
4. Scheduled Task 测试结果
5. 异常情况
6. commit hash
```

## 为什么现在做

这是整个 V2 最不确定的一环。

必须独立验证：

```text
自动读取 Prompt
↓
自动生图
↓
自动落盘
```

真正跑通之后，再往后做发送。

## 做完之后得到什么

项目目录里可以自动出现：

```text
daily_image.png
```

并且多群图片自动依次生成。

## 验收标准

- 至少连续成功生成 3 个群。
- 图片均进入正确目录。
- 顺序执行。
- 中途失败不会误把下一步当作成功。
- 已成功的图片不会重复生成。

---

# P6｜微信发送 Adapter + 自动发送测试

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P6：Windows 微信自动发送。

目标：
实现独立 WechatSender Adapter，通过 wechat-automation-api 向指定微信群发送排行榜文字和本地日报图片。

参考项目：
https://github.com/LAVARONG/wechat-automation-api

要求：

1. 不直接把第三方项目代码散落进 GroupBrief 业务逻辑。
2. 创建：
   WechatSender
   WechatAutomationSender
3. 支持：
   - health_check
   - send_text(target, text)
   - send_image(target, image_path)
4. 发送目标来自群配置 send_target。
5. 发送顺序固定：
   第一条 ranking.txt
   第二条 daily_image.png
6. 发送图片前必须验证路径存在。
7. 使用绝对路径。
8. 返回明确 success / failure。
9. 记录发送时间。
10. 先增加测试模式：
    - dry_run=true 时不真正发送
11. 增加 scripts/test_wechat_send.py。
12. 默认测试先发送到“文件传输助手”或专门测试群，禁止直接批量发送正式群。
13. Windows 运行条件：
    - 微信已登录
    - 电脑不锁屏
    - 不休眠
14. 不做复杂重试。
15. 本轮完成真实的“排行榜文字 + 图片”双发送测试。
16. 所有改动独立 commit。

完成后输出：
1. Adapter 接口
2. 第三方依赖方式
3. dry_run 测试
4. 真实发送测试结果
5. 已知限制
6. commit hash
```

## 为什么现在做

到这一轮之前，日报内容已经完全准备好。

现在只解决“最后一公里”。

## 做完之后得到什么

可以通过一个确定性函数完成：

```text
send_text()
send_image()
```

真正把 GroupBrief 结果送到微信。

---

# P7｜全流程 Pipeline + 调度

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P7：完整 Daily Pipeline 和 Scheduler。

目标：
把 P1-P6 串成一条可以每天自动运行的完整流水线。

核心流程：

08:00：
1. 加载启用群配置
2. 判断今天是否需要生成
3. 计算每个群统计周期
4. 获取聊天数据
5. 保存 messages.json
6. 生成 ranking.json
7. 渲染 ranking.txt
8. DeepSeek 生成 image_prompt.txt
9. 按群串行调用 Codex imagegen
10. 保存 daily_image.png
11. 标记 READY_TO_SEND

到每个群的 send_time：
12. 发送 ranking.txt
13. 发送 daily_image.png
14. 标记 SENT

要求：

1. 新建 DailyPipeline。
2. 每个群独立状态。
3. 某群失败不能阻塞其他群。
4. 生图阶段必须使用全局单队列串行。
5. 发送时间由每群配置。
6. 周六周日默认跳过。
7. 周一默认统计周五+周六+周日。
8. 同一群同一统计周期必须防止重复执行。
9. 同一群同一运行已 SENT 时禁止重复发送。
10. 支持手动 force_generate。
11. 支持手动 force_send。
12. 任何失败写日志。
13. 暂不做复杂自动重试。
14. 为完整 Pipeline 加集成测试。
15. 增加一个统一入口：
    python scripts/run_daily_pipeline.py
16. 所有改动独立 commit。

完成后输出：
1. 完整状态流
2. 一次 dry_run 全流程
3. 一次真实测试群全流程
4. 各阶段耗时
5. commit hash
```

## 为什么现在做

前面的轮次都是独立能力。

这一轮才真正把它们组装成 V2。

## 做完之后得到什么

只需要：

```bash
python scripts/run_daily_pipeline.py
```

就能完成一天所有群的任务。

---

# P8｜V2 前端重构

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P8：前端整体重构。

V2 前端需要按照此前已经确认的设计方向重新设计：

设计语言：
- Apple 风格
- 蓝白主色
- 大面积留白
- 简洁
- 大气
- 高级
- 不做廉价后台管理系统风格

V2 不再显示：
- 实时监听
- 实时消息流
- 机器人在线监听面板

核心页面调整为：

一、首页 Dashboard

显示：
- 今日日期
- 今日启用群数
- 今日待生成
- 已生成
- 已发送
- 失败
- 下一次发送时间
- 每个群今日状态

每个群卡片显示：
- 群名称
- 统计周期
- 发送时间
- 当前状态
- 图片缩略图（已生成时）
- 查看详情
- 立即生成
- 立即发送

二、群管理

每个群支持配置：
- 群名称
- 是否启用
- 统计周期规则
- 发送时间
- 总结模型
- Prompt 模型
- 是否生图
- 发送目标
- 排行榜模板
- 生图 Prompt 模板

支持新增群。
支持停用群。

三、模板中心

分为：
1. 排行榜模板
2. 生图 Prompt 模板

要求：
- 在线编辑
- 保存
- 预览
- 恢复默认
- 每个群可绑定不同模板

四、历史日报

按：
群 → 日期

展示：
- 统计周期
- 消息数
- 发言人数
- 排行榜
- 生图 Prompt
- AI 日报图片
- 发送状态
- 错误日志（如果失败）

V2 可以预留图片消息展示窗口，但本轮不实现群聊实时图片流。

五、系统状态

显示：
- WeChatDataAnalysis 状态
- DeepSeek 状态
- Codex 图片任务状态
- 微信发送状态
- 最近一次完整任务
- 日志入口

本轮要求：

1. 不改变后端核心业务逻辑。
2. 先做信息架构和页面重构。
3. 保持响应式。
4. 优先桌面端。
5. 页面要有真实状态，不用大量假数据硬编码。
6. 前端所有关键操作连接真实后端 API。
7. 保留危险操作确认。
8. 所有改动独立 commit。

完成后输出：
1. 页面列表
2. 页面截图
3. 交互说明
4. 后端 API 对接情况
5. commit hash
```

## 为什么现在做

直到 P7，后端全自动链路才真正稳定。

此时再重构前端，可以避免先做漂亮页面，随后因为后端变动不断返工。

## 做完之后得到什么

GroupBrief V2 从脚本工具正式变成：

```text
可配置
可观察
可管理
可回看
```

的本地产品。

---

# P9｜无人值守稳定性与开机运行

## 给 Codex 的话（可复制）

```text
开始 GroupBrief V2 P9：Windows 无人值守稳定性。

目标：
让 GroupBrief 在 Windows 长期开机、不锁屏环境下长期自动工作，而不是只能手动运行。

要求：

1. 增加启动检查：
   - WeChatDataAnalysis 是否可用
   - 微信是否登录
   - DeepSeek 配置是否可用
   - output 是否可写
   - templates 是否完整
2. 支持 GroupBrief 开机后自动启动。
3. 支持任务调度自动恢复。
4. 程序异常退出后下一次启动能恢复未完成状态。
5. SENT 任务绝不重复发送。
6. IMAGE_READY 任务可跳过重复生图。
7. 增加日志轮转，防止日志长期无限增长。
8. 增加最大日志保留天数。
9. 增加 output 文件完整性检查。
10. 不允许因为一个群失败导致整个服务退出。
11. 增加运行健康页。
12. 增加手动重跑失败任务功能。
13. Windows 休眠/锁屏风险在系统状态中给出明确提示。
14. 不绕过系统锁屏安全机制。
15. 所有改动独立 commit。

完成后输出：
1. Windows 启动方式
2. 恢复策略
3. 防重复策略
4. 日志策略
5. 连续运行测试结果
6. commit hash
```

## 为什么现在做

“自动化能运行一次”和“每天不用管它”是两回事。

最后一轮专门解决长期运行问题。

## 做完之后得到什么

GroupBrief V2 可以在你的 Windows 电脑上长期运行：

```text
早上自动开始
↓
自己取数据
↓
自己统计
↓
自己生图
↓
自己发群
```

你只在失败时才需要打开后台查看。

---

# 十三、完整开发顺序

```text
P0
V2 基线固化
↓
P1
WeChatDataAnalysis 自动取聊天记录
↓
P2
统计周期 + 排行榜
↓
P3
排行榜模板
↓
P4
DeepSeek 生图 Prompt
↓
P5
Codex $imagegen 自动生图
↓
P6
微信自动发送
↓
P7
完整 Pipeline + 调度
↓
P8
V2 前端重构
↓
P9
Windows 无人值守稳定性
```

不要并行开发全部轮次。

推荐：

```text
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7
```

严格串行。

P8 前端可以在 P7 完成后单独集中重构。

P9 最后做。

---

# 十四、为什么不一开始就写一个“大自动化 Prompt”

因为 V2 有三个高风险依赖：

1. WeChatDataAnalysis 数据读取。
2. Codex `$imagegen` 自动落盘。
3. Windows 微信 UI 自动化发送。

任何一个没验证清楚，完整 Pipeline 都可能是假闭环。

所以开发顺序必须是：

```text
先验证单点能力
↓
再封装 Adapter
↓
再组成 Pipeline
↓
最后定时运行
```

---

# 十五、V2 最终验收场景

最终必须完成一次完整真实验收。

假设测试日为周二。

系统应该自动完成：

```text
08:00
↓
读取群配置
↓
发现 5 个启用群
↓
统计上一周一 00:00:00 ~ 23:59:59
↓
从 WeChatDataAnalysis 自动获得 5 个群聊天记录
↓
自动生成 5 份 ranking.txt
↓
DeepSeek V4 Flash 自动生成 5 份 image_prompt.txt
↓
Codex 严格一张一张生成 5 张图片
↓
每张保存到对应 群/日期 目录
↓
等待各群发送时间
↓
发排行榜
↓
发图片
↓
状态更新 SENT
```

用户无需：

- 手动导出聊天记录
- 手动复制聊天内容
- 手动复制 Prompt
- 手动点生图
- 手动保存图片
- 手动复制排行榜
- 手动打开微信群
- 手动发送图片

---

# 十六、V2 完成定义

只有满足以下条件，才算 GroupBrief V2 完成：

- [ ] 不人工导出聊天记录。
- [ ] 能按群自动读取正确日期数据。
- [ ] 周一三天统计规则正确。
- [ ] 周六、周日默认不运行。
- [ ] 排行榜完全由代码统计。
- [ ] 排行榜格式可编辑。
- [ ] Emoji 在微信中正常显示。
- [ ] DeepSeek V4 Flash 能自动生成生图 Prompt。
- [ ] 生图 Prompt 模板可编辑。
- [ ] Codex 可以自动读取 Prompt。
- [ ] Codex 可以通过 `$imagegen` 生图。
- [ ] 图片按群 / 日期自动落盘。
- [ ] 多群图片严格串行生成。
- [ ] 每群支持独立发送时间。
- [ ] 微信可以自动发送排行榜。
- [ ] 微信可以自动发送本地图片。
- [ ] 已发送内容不会重复发送。
- [ ] 失败有日志。
- [ ] V2 前端不展示实时监听。
- [ ] 前端支持群管理。
- [ ] 前端支持排行榜模板编辑。
- [ ] 前端支持生图 Prompt 模板编辑。
- [ ] 前端支持查看历史日报。
- [ ] Windows 长期开机、不锁屏时可以连续自动运行。

---

# 十七、V2 之后再考虑的功能

以下功能全部留给 V2.1 / V3，不进入本轮：

- 群聊图片内容理解。
- AI 识别表情包。
- 图片消息纳入漫画素材。
- 群周报 / 月报。
- 更多排行榜。
- 群聊关键词趋势。
- 历史数据分析。
- 自动重试和告警。
- 手机端管理。
- 云端管理。
- 多微信账号。
- 多用户 SaaS。
- 企业微信 / 飞书 / Telegram 发布。
- Windows 锁屏运行。
- 服务器端微信发送。
- 更换图片 Provider。
- 多模板市场。
- 群主自助配置。
- 自动图片质量检测与重新生成。

---

# 十八、一句话总结 V2

**GroupBrief V2 = WeChatDataAnalysis 自动取数 + 代码自动排行 + DeepSeek 自动写生图 Prompt + Codex GPT Image 2 自动出图 + Windows 微信自动发布。**

V2 的核心不是继续增加更多 AI 功能，而是把现在已经能做的日报流程真正变成：

> **每天不用人碰，也能自己完成。**
