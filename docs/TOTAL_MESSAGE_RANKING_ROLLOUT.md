# 六群总消息排行榜切换

## 行为
下一次新任务使用 all_messages + default，所有可计数非系统消息各计一条。
只展示发言人数、总消息与 Top 名单。旧模板、历史排名和图片保持原样。
strict_image_fact_check 是独立布尔群配置，默认 false；本次六群设为 true。
旧 text_primary_with_interactions 继续隐式开启严格图片校验。
群 API 支持读写该字段；任务清单、run.json 和恢复路径保存该设置。
Dashboard 根据选中 ranking.json 的 count_policy 展示，缺少字段按 all_messages 兼容。

## 正式生效步骤（尚未执行）
须先完成 PR 验收并获得用户当次合并、生产更新和服务重启授权。
1. 核实正式监听进程、管理器、实际数据库路径和当天任务状态；如有在途生成或发送，等待完成。
2. 记录当天及历史产物校验值和发送状态，在停止正式服务后确认 8766 已释放，调度器和独立任务进程均已退出。
3. 在已更新代码的目录使用 Python 执行以下脚本。数据库参数必须是上一步核实的正式文件；不得使用工作目录中新建的空数据库。
4. 应用后构建前端、按原管理方式启动服务，核实监听 PID、进程 ancestry、HTTP 健康、调度状态和群配置。
5. 六个活动群 ID 23–28 应均为 all_messages / default / strict_image_fact_check=true；其他字段保持原值。核对历史产物校验值与发送状态未改变。不得通过真实取数、生图或发送来测试这次切换。

只读预览：
```powershell
python scripts/configure_total_message_ranking.py --database "已核实的数据库绝对路径"
```

取得授权且服务停止后应用：
```powershell
python scripts/configure_total_message_ranking.py --database "已核实的数据库绝对路径" --apply --service-stopped
```

脚本先验证数据库和活动群集合，再通过 SQLite backup API 生成同目录带时间戳的完整备份。
事务中补齐新列、只更新六群的三个配置字段，并检查完整性、外键和所有其他群字段。
活动群集合或旧配置发生变化时拒绝执行。脚本不读写日报产物、不调用网络业务接口。

## 回滚
配置切换后若尚无新业务写入，可在服务停止状态下使用已验证备份恢复。
若已有新业务写入，禁止整库覆盖；从备份读取六群旧配置，仅事务恢复三个字段，保留其他业务数据。
独立图片校验列可保留，新代码仍兼容旧排行榜。
