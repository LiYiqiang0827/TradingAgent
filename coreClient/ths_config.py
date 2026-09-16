"""
~/TradingAgent/coreClient/ths_config.py
同花顺(hithink-finance CLI)客户端配置

跨业务共享:
- offlineDataManager / onlineDataManager / monitor 都可以用
- 与 ~/TradingAgent/coreClient/redis_config.py 同样的配置层模式

**配置项说明**:
- THS_CLI_CMD:hithink-finance CLI 命令名(可被 PATH 解析到)
- THS_CLI_DEFAULT_TIMEOUT:CLI 单次调用超时(秒)
- THS_CLI_RETRY_MAX:CLI 失败重试次数
- THS_CLI_RETRY_BASE_SLEEP:CLI 重试基础间隔(秒,实际 sleep = base * 2^attempt)
- THS_CHUNK_SIZE_DEFAULT:批量调用每批 thscode token 数(API 硬限 100/批,默认 80 留 buffer)
- THS_POOL_SIZE_DEFAULT:涨停/跌停/炸板池默认页大小(1-200)
- THS_DEFAULT_TS_SUFFIXES:thscode 后缀(沪 .SH / 深 .SZ)— 同花顺 envelope 约定

业务含义说明:
- thscode = "000001.SZ" 形式,前 6 位是股票代码,后 3 位是市场标识
- 同花顺 CLI 接受 --thscodes "000001.SZ,600519.SH" 形式批量传入
- 增量更新:thscode 在源头已统一,无需自己拼市场
"""

# ============================================================
# hithink-finance CLI 配置
# ============================================================
THS_CLI_CMD = "hithink-finance"           # CLI 命令名(PATH 必须能解析到)
THS_CLI_DEFAULT_TIMEOUT = 30              # CLI 单次调用超时(秒)
THS_CLI_RETRY_MAX = 3                     # CLI 失败重试次数
THS_CLI_RETRY_BASE_SLEEP = 1              # 退避基础(秒,实际 sleep = base * 2^attempt)


# ============================================================
# 批量大小
# ============================================================
# API 硬限 100 thscode/批,默认 80 留 buffer
THS_CHUNK_SIZE_DEFAULT = 80

# 涨停/跌停/炸板池默认页大小(1-200)
THS_POOL_SIZE_DEFAULT = 200


# ============================================================
# ts 后缀约定(同花顺 envelope)
# ============================================================
# 沪市 .SH / 深市 .SZ
THS_DEFAULT_TS_SUFFIXES = (".SH", ".SZ")
