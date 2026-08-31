# P1.3 配置与 Provider fail-closed

日期：2026-08-25

## 结论

真实运行现在默认拒绝测试 Provider 和未知 Provider 配置。Mock/本地模板不再因为真实依赖缺失而把任务伪装成成功；设置 API 会在任何数据库写入前完成类型、Provider 名称和邮件配置校验。

```text
真实运行（默认）
  allow_test_providers=false
      ├─ History Mock：不注册
      ├─ V1 Template：不降级为成功
      ├─ 未知 summary/sender：配置失败
      └─ 邮件配置不完整：SMTP 前失败

显式自动化测试
  allow_test_providers=true
      └─ 可按测试设置启用 Mock/Template，不触发外部依赖
```

## 整改前问题

- `history_provider_mock_enabled` 默认 true，所有真实历史 Provider 不可用时会自动读 fixtures。
- history registry 接收了 Settings，却用 `cls()` 重新读取全局缓存；API 刚保存的路径可能不生效。
- 未知 history Provider 名称被静默忽略。
- V1 DeepSeek 缺 Key、或 AI 主备都失败时，本地模板会返回 `success=True`。
- summary 备用 Provider 未知值被当作“没有备用”，没有配置错误。
- `wechat_sender_mode` 只判断 `legacy_cli`，其他任意拼写都会默认选择 native。
- `ai_provider` 可在 UI/API 编辑，但真实路由只读取 `summary_provider_primary/fallback`。
- V1 history provider 选择可在 UI/API 编辑，但正式 V2 始终使用 WeChatDataAnalysis MCP/导出。
- 邮件只检查 enabled/host；缺 recipient/from、端口错误或账号缺密码时仍可能进入构造/SMTP。

## 修改

### 测试 Provider 安全闸门

- 新增环境级 `allow_test_providers=false`，不通过数据库或设置 API 开启。
- `history_provider_mock_enabled` 默认 false；即使旧数据库仍存 true，只要安全闸门关闭，registry 也不会追加 Mock。
- 显式把 `mock` 配成主/备 Provider 且安全闸门关闭时，直接抛配置错误。
- V1 Template 只在安全闸门开启时用于测试；真实运行 AI 失败会保持失败。

### 配置真正传递

- history registry 把同一个 Settings 实例传给 WeChatDataAnalysis、wechat-cli 和 Mock。
- WeChatDataAnalysis 的微信目录探测改用实例 Settings，不再回到全局缓存。
- history/summary/sender 未知名称均 fail-closed。
- 图片 Provider 的健康预检移到全局生图锁之前；CLI 配置错误不会因另一个真实生图任务持锁而等待最长一小时，进入锁后仍会二次检查。

### 设置 API 与 UI

- API 先复制运行时 Settings、完成类型和业务校验，再写数据库并应用运行时值。
- 非法布尔值不再按“任意非空字符串=true”处理。
- 生产设置入口移除 Mock 开关、V1-only history provider 选择和无实际路由作用的 `ai_provider`。
- summary、sender 和邮件仍是正式可编辑配置，错误值返回 HTTP 422，且不会产生部分设置写入。

### 邮件预检

真实发送前统一检查：

- email enabled
- SMTP host
- 1–65535 端口
- recipient
- from 或 SMTP user
- 配置 SMTP user 时必须有 password

调度发现“邮件已启用但配置不完整”时写入 `email_status=failed_config`、`EMAIL_PROVIDER_CONFIG_INVALID`，整批返回 partial，并且不启动邮件子进程。

## 配置边界

- V2 历史读取唯一正式入口仍是 WeChatDataAnalysis MCP/JSON 导出；没有为追求统一而引入新的数据源抽象。
- V1 history provider 字段仅保留环境/旧数据库兼容，为 P1.5 冻结退役做准备。
- Codex → DeepSeek 是两个真实 Provider 之间的显式 fallback，继续保留。
- V2 Prompt 的确定性版式 fallback 不是外部 Provider Mock，不在本轮删除。
- 邮件整体关闭仍是合法配置；只有“已启用但配置残缺”才是失败。

## 验证

- 主工作区定向测试：87 项通过（2.85 秒）。
- 图片 Provider/锁顺序补充定向测试：52 项通过（2.32 秒）；图片任务测试文件全量 30 项通过（1.88 秒）。
- 最新隔离快照其余测试 510 项通过（26.04 秒），与图片任务 30 项合计覆盖当前 540 项测试。
- 隔离快照 Python compileall 通过；最后两项图片改动另行通过 `py_compile`。
- 隔离前端 production build 通过：TypeScript project build 和 Vite build 成功，4596 个模块完成转换。
- `git diff --cached --check` 通过。
- 全局生成 mutex 在验证前已确认可用；此前占锁的 23–28 号群 Prompt 重建任务已自然结束，未强制终止。
- 首次全量验证又发现 8766 正在真实生图时持有图片 mutex；测试进程已停止且未终止真实生图，并据此修复了 Provider 健康预检与锁的先后顺序。
- 两次 Operator 全量测试均因与真实生图 mutex 竞争而超出既有时长基线；按执行边界停止重试后，主控通过独立 mutex 的图片测试和排除图片文件的隔离全量测试完成验收。
- 未调用真实 AI、SMTP、微信或其他外部 Provider。

## 部署说明

本轮代码提交后需要在工作区无未验证并行生产改动时安全重启 8766，才能让常驻进程加载新安全边界。当前另一个 Prompt/UI 任务仍有未提交文件，因此本轮不把它们一起加载进服务。

## 回滚

- 代码可整体 revert 本次提交；没有 Schema 变更，也没有修改真实数据库设置值。
- 回滚后 Mock/Template 的旧 fail-open 行为会恢复，因此只应用于紧急代码回退，不应作为长期配置方案。
