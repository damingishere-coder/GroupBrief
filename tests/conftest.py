"""pytest 会话级配置。

测试必须与真实运行环境隔离：
- 使用独立测试数据库（避免加载 data/groupbrief.db 中用户真实配置，
  例如已保存的 DeepSeek API Key / SMTP / MCP token，防止测试触发真实外部调用）；
- 禁用调度器，避免 TestClient 启动 app 时后台任务干扰测试。
注意：必须在任何 app 模块被 import 之前设置环境变量
（get_settings() 为 lru_cache，首次调用后不再读取环境变量）。
"""

import os
import random
import tempfile
import uuid
from pathlib import Path

_TEST_DB_PATH = (
    Path(tempfile.gettempdir())
    / f"groupbrief-pytest-{os.getpid()}-{uuid.uuid4().hex}.db"
)

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["GROUPBRIEF_NO_SCHEDULER"] = "1"
# 测试不读取真实微信联系人库（避免本机 APPDATA 下的 contact.db 影响断言）
os.environ["GROUPBRIEF_NO_CONTACT_DB"] = "1"
# 屏蔽 .env / 用户环境中的真实外部配置，防止测试触发真实 MCP / AI / 邮件调用
os.environ["WECHAT_MCP_URL"] = ""
os.environ["WECHAT_MCP_TOKEN"] = ""
os.environ["WECHAT_MCP_ACCOUNT"] = ""
os.environ["AI_API_KEY"] = ""
# 默认集成测试强制使用无需外部调用的旧兼容分支；Codex 主备路由由专门单测覆盖。
os.environ["SUMMARY_PROVIDER_PRIMARY"] = "deepseek"
# 旧 V1 单测需要显式进入兼容维护模式；生产默认仍是 read_only。
os.environ["LEGACY_V1_WRITE_MODE"] = "maintenance"


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--random-order-seed",
        action="store",
        type=int,
        default=None,
        help="使用给定整数 seed 随机重排测试收集顺序",
    )


def pytest_collection_modifyitems(config, items) -> None:
    seed = config.getoption("--random-order-seed")
    if seed is None:
        return
    random.Random(seed).shuffle(items)
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"random-order-seed={seed}")


def pytest_sessionfinish(session, exitstatus) -> None:
    """释放并删除本次 pytest 独占的临时 SQLite 文件。"""
    del session, exitstatus
    try:
        from app.db import repository as repo

        if repo.engine is not None:
            repo.engine.dispose()
    except Exception:
        # 测试收尾不能覆盖更早、更有价值的失败信息。
        pass

    for candidate in (
        _TEST_DB_PATH,
        Path(f"{_TEST_DB_PATH}-wal"),
        Path(f"{_TEST_DB_PATH}-shm"),
        Path(f"{_TEST_DB_PATH}-journal"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
