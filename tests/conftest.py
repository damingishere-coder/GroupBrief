"""pytest 会话级配置。

测试必须与真实运行环境隔离：
- 使用独立测试数据库（避免加载 data/groupbrief.db 中用户真实配置，
  例如已保存的 DeepSeek API Key / SMTP / MCP token，防止测试触发真实外部调用）；
- 禁用调度器，避免 TestClient 启动 app 时后台任务干扰测试。
注意：必须在任何 app 模块被 import 之前设置环境变量
（get_settings() 为 lru_cache，首次调用后不再读取环境变量）。
"""

import os

os.environ["DATABASE_URL"] = "sqlite:///data/test_groupbrief.db"
os.environ["GROUPBRIEF_NO_SCHEDULER"] = "1"
# 测试不读取真实微信联系人库（避免本机 APPDATA 下的 contact.db 影响断言）
os.environ["GROUPBRIEF_NO_CONTACT_DB"] = "1"
# 屏蔽 .env / 用户环境中的真实外部配置，防止测试触发真实 MCP / AI / 邮件调用
os.environ["WECHAT_MCP_URL"] = ""
os.environ["WECHAT_MCP_TOKEN"] = ""
os.environ["WECHAT_MCP_ACCOUNT"] = ""
os.environ["AI_API_KEY"] = ""
