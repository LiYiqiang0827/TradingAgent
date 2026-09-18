"""
~/TradingAgent/coreClient/tushare_config.py
Tushare API 配置(限流 + 重试)

token 已从代码中移除,改用环境变量 TRADING_AGENT_TUSHARE_TOKEN:
  export TRADING_AGENT_TUSHARE_TOKEN=<your_token>
或在 ~/.zshrc 里设一次。

配置统一来源;offlineDataManager / onlineDataManager / 未来的 MyATM 替代
共享同一份限流 + 重试配置(token 各自从环境变量读)。
"""
import os


# ============================================================
# Tushare API token(从环境变量读,严禁硬编码)
# ============================================================
TUSHARE_TOKEN = os.environ.get("TRADING_AGENT_TUSHARE_TOKEN", "")

if not TUSHARE_TOKEN:
    import warnings
    warnings.warn(
        "未设置 TRADING_AGENT_TUSHARE_TOKEN 环境变量。"
        "Tushare API 调用会失败。"
        "设置方法:export TRADING_AGENT_TUSHARE_TOKEN=<your_token>"
    )


# ============================================================
# API 限流
# ============================================================
# Tushare pro 限制 200 次/分钟
TUSHARE_RATE_LIMIT_PER_MIN = 200


# ============================================================
# 重试
# ============================================================
# 失败重试次数
TUSHARE_RETRY_MAX = 3

# 失败重试基础间隔(秒,实际 sleep = TUSHARE_RETRY_SLEEP * (attempt + 1))
TUSHARE_RETRY_SLEEP = 2
