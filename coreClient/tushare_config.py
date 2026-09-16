"""
~/TradingAgent/coreClient/tushare_config.py
Tushare API 配置(token + 限流 + 重试)

这是 tushare_client 的**唯一**配置来源;offlineDataManager/scripts/config/settings.py
里曾经有 TUSHARE_TOKEN 等配置,已删除,统一在这里管理。

多个工程(offlineDataManager / onlineDataManager / 未来的 MyATM 替代)共享
同一份 token 配置。
"""
# ============================================================
# Tushare API token
# ============================================================
# 复用 MyATM 的 token,避免重复维护
TUSHARE_TOKEN = "328371bc416729ed6291ee1a032818594722e3dbcd4201486720844e"

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
