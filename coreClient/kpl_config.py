"""
~/TradingAgent/coreClient/kpl_config.py
开盘啦(kpl)API 配置:auth_token + 限流 + 重试

跟 tushare_config 同构;多个工程(offlineDataManager / onlineDataManager / 未来的 quant-strategy)
共享同一份配置。
"""
import os

# ============================================================
# 认证
# ============================================================
# 开盘啦个人认证 token,从 App 抓包或登录接口提取
# 当前值跟 ~/.kpl-api/.auth.json 保持同步(改一处即可)
KPL_AUTH_TOKEN = "0"  # TODO: 替换为真实 token(未登录时用 0 占位)
KPL_USER_ID = "0"     # TODO: 替换为真实 user_id

# 备用:从 ~/.kpl-api/.auth.json 自动读取(优先于上面硬编码)
_KPL_AUTH_FILE = os.path.expanduser("~/.kpl-api/.auth.json")


def get_kpl_auth() -> tuple:
    """返回 (token, user_id),优先从 ~/.kpl-api/.auth.json 读,失败则用 kpl_config

    Returns:
        (token: str, user_id: str)
    """
    import json
    if os.path.exists(_KPL_AUTH_FILE):
        try:
            with open(_KPL_AUTH_FILE) as f:
                d = json.load(f)
            token = d.get("token") or d.get("Token") or KPL_AUTH_TOKEN
            user_id = d.get("user_id") or d.get("UserID") or KPL_USER_ID
            return (str(token), str(user_id))
        except Exception:
            pass
    return (KPL_AUTH_TOKEN, KPL_USER_ID)


# ============================================================
# API hosts
# ============================================================
# 不同 host 对应不同的子服务(参考 trading:kpl-api skill)
KPL_HOST_APPHWHQ = "apphwhq.longhuvip.com"   # 实时盯盘
KPL_HOST_APPHIS = "apphis.longhuvip.com"     # 历史
KPL_HOST_APPLHB = "applhb.longhuvip.com"     # 龙虎榜
KPL_HOST_APPPHQ = "apphq.longhuvip.com"      # 行情/复盘

KPL_BASE_URL = "https://{host}/w1/api/index.php"


# ============================================================
# 限流
# ============================================================
# KPL 没有官方限流值;保守设 30 次/分钟(个人账号足够)
KPL_RATE_LIMIT_PER_MIN = 30


# ============================================================
# 重试
# ============================================================
KPL_RETRY_MAX = 3
KPL_RETRY_SLEEP = 2  # 基础间隔(秒,实际 sleep = KPL_RETRY_SLEEP * (attempt + 1))
