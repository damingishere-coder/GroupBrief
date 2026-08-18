# GroupBrief V2 用 Docker 启动（保姆级）

> 把 GroupBrief 的服务（网页 + 后端 + 自动任务）装进一个"集装箱"（Docker 容器）里，
> 一条命令就能启动/停止，数据都保存在你电脑的文件夹里。

## 一、先了解：容器里装了什么，没装什么

| 内容 | 是否在容器里 |
| --- | --- |
| GroupBrief 网页 + 后端 + 每日自动任务 | ✅ 在容器里 |
| 你的数据（数据库 / 日报文案 / 日志） | ✅ 存在你电脑的 `data/` `output/` `logs/` 文件夹（容器"借用"） |
| 读取微信群聊记录（WeChatDataAnalysis 桌面软件） | ❌ 在容器**外面**（它需要 Windows 桌面） |
| 自动发送微信 | ❌ 在容器外面（UI 自动化需要 Windows 桌面和微信窗口） |
| DeepSeek 生成文案 | ✅ 在容器里（联网调用） |

一句话：**容器负责"大脑"（统计、排版、调度），外面负责"手脚"（读微信、发微信）**。

## 二、第一次启动（大约 3~5 分钟）

1. **确认 Docker Desktop 已打开**：任务栏右下角有 Docker 图标，是绿色的。
   没有的话：开始菜单搜"Docker Desktop"打开，等它变成绿色。

2. **打开终端**：在项目文件夹（`C:\Users\10578\Documents\AI - GroupBrief`）空白处
   按住 `Shift` 点右键 → "在此处打开 PowerShell 窗口"（或"在终端中打开"）。

3. **复制这一条命令，回车**：
   ```bash
   docker compose up -d --build
   ```
   - 第一次会下载基础镜像、构建前端、装依赖，需要几分钟，请耐心等。
   - 看到 `Container groupbrief-v2  Started` 就成功了。

4. **打开浏览器**，访问：`http://127.0.0.1:8766`
   - 应该能看到 GroupBrief 的五个页面（今日概览 / 群管理 / 模板中心 / 历史日报 / 系统状态）。

## 三、日常使用

| 想做什么 | 命令 |
| --- | --- |
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down` |
| 查看状态 | `docker compose ps` |
| 查看日志 | `docker compose logs -f` |
| 更新代码后重新构建启动 | `docker compose up -d --build` |

## 四、你的日报文案存在哪（重点）

容器产生的所有文件，都保存在项目文件夹里，**按"群名 / 日期"分好文件夹**：

```
output/
└─ 茶馆V3.0（三周年纪念）🐮🐴/
   └─ 2026-08-18/                ← 日期文件夹
      ├─ ranking.txt             ← 排行榜文字（可直接复制到微信群）
      ├─ image_prompt.txt        ← 生图文案（给 Codex 用的）
      ├─ ranking.json            ← 排行榜数据
      ├─ messages.json           ← 当天聊天记录（存档）
      └─ run.json                ← 运行状态
```

**Codex 生图怎么做**（你现在要从 Codex 那边开发）：
1. 打开 `output/群名/日期/image_prompt.txt`，复制文案给 Codex；
2. Codex 生成图片后，**把图片保存为** `output/群名/日期/daily_image.png`；
3. 刷新网页"今日概览"，该群卡片就会显示图片，可以"立即发送"。

> 注意：文件夹名会自动把微信名里的特殊符号（`\ / : * ? " < > |`）换成 `-`，
> 中文和表情符号保留，方便 Windows 管理。

## 五、常见问题

**1. 网页打不开 / 一直转圈**
- 先确认容器在跑：`docker compose ps`（STATUS 应为 Up）
- 看日志有没有报错：`docker compose logs --tail 50`

**2. "系统状态"里 WeChatDataAnalysis 显示不可用**
- 容器是靠 `host.docker.internal` 找到你电脑上 WeChatDataAnalysis 的；
- 请确认：桌面软件 WeChatDataAnalysis 已打开、电脑不锁屏；
- 再点"重新检测"。

**3. 微信发送显示不可用**
- 微信 UI 自动化需要 Windows 桌面和已登录的微信，这是**设计如此**（容器不负责发微信）；
- 等微信发送方案在宿主机跑通后，网页"系统状态"会自动显示可用。

**4. 端口被占用**
- 8766 被别的程序占了：把 `docker-compose.yml` 里 `"8766:8766"` 改成
  `"8767:8766"`，然后 `docker compose up -d`，访问 `http://127.0.0.1:8767`。

**5. 想用回以前的 Windows 直跑方式**
- 完全不受影响：双击 `start_windows.bat` 还是老样子（和 Docker 用同一份数据文件夹）。

## 六、隐私说明
- 聊天数据只存在你电脑上（`data/`、`output/`），不会上传到别处；
- 只有生成文案所需的文本会发给 DeepSeek API；
- 容器密码（MCP token / DeepSeek Key）从你项目的 `.env` 读取，不写进镜像。
