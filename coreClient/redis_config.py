"""
~/TradingAgent/coreClient/redis_config.py
Redis 通用配置(连接参数 + 通用 meta 前缀)

**不包含业务前缀**:业务前缀(如 online / monitor)由各业务的 redis_*.py 自行管理。
设计参照 ~/TradingAgent/coreClient/tushare_config.py。

跨业务共享:
- offlineDataManager / onlineDataManager / monitor / kpl-api / 未来的 MyATM 替代
  都用同一份 Redis 连接配置 + 通用 meta 键命名约定。
"""
# ============================================================
# Redis 连接参数
# ============================================================
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6379
DEFAULT_DB = 0


# ============================================================
# 通用 meta 键前缀
# ============================================================
# 各类 stream 落盘游标的统一前缀
# 例:META_LAST_ID_PREFIX + "anomaly" → "last_stream_id:anomaly"
META_LAST_ID_PREFIX = "last_stream_id:"

# 通用 meta 字段 key 名(用于 HSET 后的 get_meta key)
META_KEY_LAST_ID = "last_id"
META_KEY_COUNT = "count"
META_KEY_TRADE_DATE = "trade_date"


# ============================================================
# STREAM field 通用 drop_keys
# ============================================================
# 哪些字段不写入 STREAM(过大或临时)
# 各业务可以再追加自己的 drop_keys
DEFAULT_DROP_KEYS = frozenset({
    "thscode", "ticker",
    "data_timestamp", "data_timestamp_iso",
    "save_timestamp", "save_timestamp_iso",
    "snap_ts_unix", "snap_ts",
    "fetch_timestamp",   # 拉取时间(冗余)
    "raw",               # 原始数据(冗余)
})


# ============================================================
# Redis 通用 TTL(秒)— 仅用于跨业务的默认行为
# ============================================================
# stream 的默认 ttl(秒),业务可以覆盖
DEFAULT_STREAM_TTL = 3600 * 6            # 6 小时

# ZSET window 的默认 ttl(秒),业务可以覆盖
DEFAULT_WINDOW_TTL = 300                 # 5 分钟

# LIST 的默认 max_size(最近 N 条),业务可以覆盖
DEFAULT_LIST_MAX_SIZE = 500

# SET 的默认无 expire(业务自己决定)


# ============================================================
# 重试 / 健康检查
# ============================================================
REDIS_RETRY_MAX = 3
REDIS_RETRY_SLEEP = 0.5                  # 失败重试基础间隔(秒)
REDIS_PING_TIMEOUT = 2                   # ping 超时(秒)
