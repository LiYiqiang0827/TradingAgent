"""
~/TradingAgent/coreClient/tdx_client.py
通达信(pytdx)客户端封装 + 批量 + IP 池 failover

为什么需要这个:
- hithink-finance 没有 5档盘口、没有内/外盘
- pytdx 原生支持 get_security_quotes(完整 5档盘口 + s_vol/b_vol)
- pytdx 还支持 get_minute_time_data(实时 1分钟 K,缺时间戳,不需要传 date)
- pytdx 还支持指数 5档(替代 hithink-finance index.snapshot)

批量限制(实测):
- get_security_quotes:最多 80 只/次(超过会被截断)— 见 tdx_config.TDX_QUOTE_BATCH_MAX
- get_minute_time_data:1 只/次(无批量)— 见 tdx_config.TDX_MINUTE_BATCH_MAX
- 指数 5 档:80 只/次

性能(单 IP,180.153.18.170):
- 80 只 5档盘口:15-25ms
- 1 只 1分钟K:13ms(~70 票/秒串行)
- 4 个指数 5档:20ms

可用 IP(实测 2026-09-09):
- 180.153.18.170 / 180.153.18.172:80 / 123.125.108.14 / 218.6.170.47

2026-09-11 新增:IP 池 + 自动 failover
- 单 IP 连不上/不出数据时,自动切下一个
- 用 round-robin 减少单 IP 频率

2026-09-12 v3:从 onlineDataManager 移到 coreClient/(跨业务共用)
"""
import sys
import time
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# 让 from coreClient.tdx_config import ... 工作
_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from coreClient.tdx_config import (
    TDX_IP_POOL,
    TDX_DEFAULT_PORT,
    INDEX_CODES,
    INDEX_NAMES,
    TDX_RETRY_MAX,
    TDX_RETRY_SLEEP,
    TDX_TIMEOUT,
    TDX_QUOTE_BATCH_MAX,
    TDX_MINUTE_BATCH_MAX,
    # 2026-09-12 新增:历史数据相关
    TDX_TICKS_PAGE_SIZE,
    TDX_TICKS_MAX_PAGES,
    TDX_TICKS_RETRY_BACKOFF,
    TDX_MORNING_BARS,
)

logger = logging.getLogger("tdx_client")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 2026-09-12 新增:ts_code 解析 + datetime 计算 helpers
# ============================================================

# SH=1, SZ=0, BJ=2
_MARKET_OF_SUFFIX = {"SH": 1, "SZ": 0, "BJ": 2}


def market_of(ts_code: str) -> int:
    """ts_code -> pytdx market 编码(SH=1, SZ=0, BJ=2)

    用法:
        market = market_of("000001.SZ")   # 0
    """
    if "." not in ts_code:
        raise ValueError(f"ts_code 必须含 . 后缀: {ts_code}")
    suffix = ts_code.split(".")[-1].upper()
    if suffix not in _MARKET_OF_SUFFIX:
        raise ValueError(f"未知市场后缀: {ts_code} (期望 SH/SZ/BJ)")
    return _MARKET_OF_SUFFIX[suffix]


def code_of(ts_code: str) -> str:
    """ts_code -> 6 位数字代码

    用法:
        code = code_of("000001.SZ")   # '000001'
    """
    return ts_code.split(".")[0]


def _date_int_to_str(date_int: int) -> str:
    """YYYYMMDD int -> 'YYYY-MM-DD' 字符串(用于 trade_date 字段)

    例:20260512 -> '2026-05-12'
    """
    s = str(int(date_int))
    if len(s) != 8:
        raise ValueError(f"date 必须是 8 位 YYYYMMDD: {date_int}")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _minute_index_to_datetime(idx: int, date_int: int) -> str:
    """minute K 线索引 0-239 -> 'YYYY-MM-DD HH:MM:SS'

    索引规则(2026-09-12 用户确认):
        0-119  = 09:31-11:30(上午 120 根)
        120-239 = 13:01-15:00(下午 120 根)

    pytdx 不会返回时间,客户端按序号推算。

    不完整的 K 线(数据缺失)只填实有的索引,不会补"缺失"的时间。
    """
    date_str = _date_int_to_str(date_int)
    if idx < TDX_MORNING_BARS:
        # 09:31 + idx 分钟
        minutes = 9 * 60 + 31 + idx
    else:
        # 13:01 + (idx - 120) 分钟
        minutes = 13 * 60 + 1 + (idx - TDX_MORNING_BARS)
    hour, minute = divmod(minutes, 60)
    return f"{date_str} {hour:02d}:{minute:02d}:00"


def _ticks_time_to_datetime(time_str: str, date_int: int) -> str:
    """ticks time 字段 'HH:MM' -> 'YYYY-MM-DD HH:MM:00'

    pytdx tick time 只到分钟,秒钟补 00。
    """
    date_str = _date_int_to_str(date_int)
    # time_str 形如 "10:31"
    h, m = time_str.split(":")
    return f"{date_str} {int(h):02d}:{int(m):02d}:00"


class TdxClient:
    """pytdx 客户端 + 批量封装 + IP 池 failover"""

    def __init__(self, ip: str | None = None, port: int = TDX_DEFAULT_PORT):
        """连接一个 IP(默认从池里随机挑)

        Args:
            ip: 指定 IP(测试用);None 则从池里随机
            port: 默认 TDX_DEFAULT_PORT(7709)
        """
        from pytdx.hq import TdxHq_API
        self.api = TdxHq_API()

        if ip:
            self.ip = ip
            self.port = port
        else:
            # 随机挑一个起点,失败就轮下一个
            self.ip, self.port = random.choice(TDX_IP_POOL)

        self._connect()

    def _connect(self):
        ok = self.api.connect(self.ip, self.port)
        if not ok:
            raise RuntimeError(f"pytdx connect failed: {self.ip}:{self.port}")

    def try_failover(self) -> bool:
        """当前 IP 失败时,换一个 IP 重连。成功返回 True"""
        # 找下一个未用过的
        current_idx = next(
            (i for i, p in enumerate(TDX_IP_POOL) if p == (self.ip, self.port)),
            -1,
        )
        next_idx = (current_idx + 1) % len(TDX_IP_POOL)
        # 试 TDX_RETRY_MAX 个
        for _ in range(TDX_RETRY_MAX):
            ip, port = TDX_IP_POOL[next_idx]
            try:
                self.api.disconnect()
            except Exception:
                pass
            self.ip, self.port = ip, port
            try:
                if self.api.connect(ip, port):
                    return True
            except Exception:
                pass
            next_idx = (next_idx + 1) % len(TDX_IP_POOL)
        return False

    def disconnect(self):
        try:
            self.api.disconnect()
        except Exception:
            pass

    # ============ 5档盘口 ============

    def get_orderbook(self, codes: List[Tuple[int, str]]) -> list[dict]:
        """
        批量拉 5档盘口(最多 TDX_QUOTE_BATCH_MAX 只/批)。

        2026-09-11:若返回空,自动 failover 到下一个 IP 重试。
        2026-09-11:每条 quote 都打上 data_timestamp(数据时间,数据源时间)。
        """
        if not codes:
            return []
        # pytdx 硬限 TDX_QUOTE_BATCH_MAX,超过截断
        if len(codes) > TDX_QUOTE_BATCH_MAX:
            codes = codes[:TDX_QUOTE_BATCH_MAX]

        for attempt in range(TDX_RETRY_MAX):  # 最多试 TDX_RETRY_MAX 个 IP
            try:
                quotes = self.api.get_security_quotes(codes) or []
                if quotes:
                    # ★ v3 数据时间戳:orderbook_timestamp
                    return _stamp_data_timestamp(quotes, field="orderbook_timestamp")
            except Exception:
                quotes = []
            # 返回空或异常,切 IP
            if attempt < TDX_RETRY_MAX - 1:
                self.try_failover()
        return _stamp_data_timestamp(quotes, field="orderbook_timestamp")  # 都失败,返最后尝试的结果(可能空)

    def get_orderbook_batched(self, codes: List[Tuple[int, str]], batch_size: int = TDX_QUOTE_BATCH_MAX) -> list[dict]:
        """
        自动分批拉 5档盘口,处理 > TDX_QUOTE_BATCH_MAX 只的情况

        Args:
            codes: 全部 ts_code 列表
            batch_size: 每批大小(默认 TDX_QUOTE_BATCH_MAX)

        Returns:
            全部 quote list(已打 data_timestamp)
        """
        if not codes:
            return []
        if len(codes) <= batch_size:
            return self.get_orderbook(codes)
        all_quotes = []
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            all_quotes.extend(self.get_orderbook(batch))
        return all_quotes

    # ============ 1分钟K ============

    def get_minute_kline(self, ts_code: str) -> list[dict]:
        """
        拉 1 只 1 天的 1分钟 K 线(共 240 根)

        2026-09-13 v4 改造:
          - 改用 pytdx 实时分时接口 get_minute_time_data(market, code)
            (不需要传 date,接口内部取"今天已走的分时")
          - datetime 拼法和历史接口完全一致:_minute_index_to_datetime(idx, date)
            date 用今天(YYYYMMDD 整数)
          - 去掉 date 参数(2026-09-13 用户原话:"实时分时图不需要传trade date")

        Args:
            ts_code: 6 位代码 + 市场后缀,如 "000006.SZ"

        Returns:
            list of dict,每根 K 线字段:
                ts_code               str      "000006.SZ"
                trade_date            str      "2026-09-13"(今天)
                datetime              str      "2026-09-13 09:31:00"
                time_idx              int      0-239
                price                 float
                vol                   int
                data_timestamp        str      "2026-09-13T01:25:15"

            盘中:已走的分钟数(0..N,N < 240)
            收盘后:全 4 小时约 240 根
        """
        market = market_of(ts_code)
        code = code_of(ts_code)
        # 2026-09-13 v4:实时分时接口,不传 date
        rows = self.api.get_minute_time_data(market, code)
        if not rows:
            return []

        date_int = int(datetime.now().strftime("%Y%m%d"))
        trade_date_str = _date_int_to_str(date_int)
        ts_wall = time.time()
        ts_iso = datetime.fromtimestamp(ts_wall).isoformat(timespec="milliseconds")
        out = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                row = dict(row)
            row["ts_code"] = ts_code
            row["trade_date"] = trade_date_str
            row["datetime"] = _minute_index_to_datetime(i, date_int)
            row["time_idx"] = i
            row["data_timestamp"] = ts_iso
            out.append(row)
        return out

    def get_history_minute(self, ts_code: str, date: int) -> list[dict]:
        """
        拉 1 只 1 天的历史 1分钟 K 线(共 240 根)

        2026-09-13 新增:对齐 policy_minute.db 落盘表的 6 列字段
          - 字段跟 get_minute_kline(实时)完全一致,但不带 data_timestamp
            (data_timestamp 是落盘审计字段,data_gen.py 自己加 created_at)
          - datetime 拼法:_minute_index_to_datetime(idx, date)

        Args:
            ts_code: 6 位代码 + 市场后缀,如 "000006.SZ"
            date:    YYYYMMDD 整数,例 20260911

        Returns:
            list of dict,每根 K 线字段:
                ts_code               str      "000006.SZ"
                trade_date            str      "2026-09-11"
                datetime              str      "2026-09-11 09:31:00"
                time_idx              int      0-239
                price                 float
                vol                   int
            失败时返回空 list
        """
        market = market_of(ts_code)
        code = code_of(ts_code)
        # pytdx 历史分时接口:YYYYMMDD int
        rows = self.api.get_history_minute_time_data(market, code, int(date))
        if not rows:
            return []

        date_int = int(date)
        trade_date_str = _date_int_to_str(date_int)
        out = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                row = dict(row)
            row["ts_code"] = ts_code
            row["trade_date"] = trade_date_str
            row["datetime"] = _minute_index_to_datetime(i, date_int)
            row["time_idx"] = i
            out.append(row)
        return out

    # ============ 指数 ============

    def get_index_orderbook(self, index_codes: List[Tuple[int, str]]) -> list[dict]:
        """
        拉指数 5档盘口(同 get_orderbook,只是传入的是指数代码)

        已验证的指数代码(见 tdx_config.INDEX_CODES):
            (1, '000001') 上证指数
            (0, '399001') 深圳成指
            (0, '399006') 创业板指
            (1, '000688') 科创50
        """
        return self.get_orderbook(index_codes)

    # ============ 历史 ticks(分笔成交)============

    def get_history_ticks(
        self,
        ts_code: str,
        date: int,
        max_pages: int = TDX_TICKS_MAX_PAGES,
    ) -> list[dict]:
        """
        拉 1 只 1 天的全部分笔成交(自动分页 + 去重)

        2026-09-12 新增:封装原 agent4_ticks.py 的拉取逻辑

        pytdx 限制:
        - 每次最多 2000 笔(从 start 开始)
        - 1 只 1 天,无批量
        - 客户端按 start 循环分页拉

        字段自动补全:
        - ts_code     str    "000006.SZ"
        - trade_date  str    "2026-05-12"
        - datetime    str    "2026-05-12 10:31:00"(time 字段补 :00)
        - seqId       int    从 0 开始累加(0, 1, 2, ...)
        - time        str    pytdx 原始 "HH:MM"
        - price       float
        - vol         int
        - buyorsell   int    0/1/2

        Args:
            ts_code:   6 位代码 + 市场后缀
            date:      YYYYMMDD 整数
            max_pages: 分页上限(默认 50 = 100k 笔,极端涨停日防呆)

        Returns:
            list of dict,每天的全部 tick
            失败时返回空 list(已打 logger.warning)
        """
        market = market_of(ts_code)
        code = code_of(ts_code)

        # 2026-09-13 修正:pytdx 服务器返回的 ticks 是**逆序分页**——
        #   start=0    → 最新成交(下午/收盘前)
        #   start=2000 → 较早成交(上午)
        #   start=4000 → 0 笔(超出范围)
        # 每页**内部**是时间正序,但页与页之间是逆序(先拼 start=大,再拼 start=小)
        # 此外,实测发现即便如此拼接,个别页面顺序仍可能乱序,故最后做一次保险排序
        pages: list[list[dict]] = []  # 按 page_idx 顺序收集
        seen: set[tuple] = set()  # (time, price, vol) 去重(分页边界可能重叠)

        for page_idx in range(max_pages):
            start = page_idx * TDX_TICKS_PAGE_SIZE

            # 单页拉取 + retry
            chunk: list = []
            for attempt, backoff in enumerate([0] + TDX_TICKS_RETRY_BACKOFF):
                try:
                    chunk = self.api.get_history_transaction_data(
                        market, code, start=start, count=TDX_TICKS_PAGE_SIZE, date=date,
                    )
                    break
                except Exception as e:
                    if attempt < len(TDX_TICKS_RETRY_BACKOFF):
                        wait = TDX_TICKS_RETRY_BACKOFF[attempt]
                        logger.warning(
                            f"get_history_ticks {ts_code} {date} page={start} "
                            f"异常 {type(e).__name__}: {e},退避 {wait}s"
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            f"get_history_ticks {ts_code} {date} page={start} "
                            f"重试 {len(TDX_TICKS_RETRY_BACKOFF)} 次仍失败"
                        )
                        # 单页失败:返回已拉到的部分(不抛异常,让上层决定怎么处理)
                        return _enrich_ticks(_reverse_concat_and_sort(pages, seen), ts_code, date)

            if not chunk:
                # 0 笔:非交易日 / 数据缺失 / 超出末尾
                if page_idx == 0:
                    logger.info(f"get_history_ticks {ts_code} {date} 无数据(0 笔),跳过")
                break

            # 去重(用 (time, price, vol) 三元组)
            page_unique: list[dict] = []
            for r in chunk:
                if not isinstance(r, dict):
                    r = dict(r)
                key = (r.get("time", ""), r.get("price", 0), r.get("vol", 0), r.get("buyorsell", 0))
                if key in seen:
                    continue
                seen.add(key)
                page_unique.append(r)
            pages.append(page_unique)

            # 最后一页判断:返回数 < page_size
            if len(chunk) < TDX_TICKS_PAGE_SIZE:
                break

            # 达到 max_pages 上限,警告
            if page_idx == max_pages - 1:
                logger.warning(
                    f"get_history_ticks {ts_code} {date} 达到 max_pages={max_pages},截断"
                )

        return _enrich_ticks(_reverse_concat_and_sort(pages, seen), ts_code, date)

    # 2026-09-18 新增:如果调用方需要分页元信息(完整性/去重数量/buyorsell 集合),
    # 用 tdx_ticks_meta.fetch_history_ticks_with_meta(client, ts_code, date) 而不是本方法。
    # 本方法保留旧签名以兼容现有调用方;不抛异常(单页失败静默返回已拉部分,无法区分完整性)。


def _stamp_data_timestamp(items: list, *, field: str = "orderbook_timestamp") -> list:
    """2026-09-11 新增:给 pytdx 返回的每条数据打上数据时间戳

    2026-09-12 v3 精简:数据时间戳字段名按调用方传入(默认 orderbook_timestamp,get_minute_kline 用 minute_timestamp)

    简化原则(用户 2026-09-11 决定,ths_client / pytdx_client 一致):
        - 数据源自带的字段原样保留,**不解析**
        - 统一在 client 拿到数据的瞬间打 <field>(wall clock)

    输出字段:
        - <field>              浮点秒(unix,client 拿到数据的瞬间)
        - <field>_iso          ISO 格式
    """
    if not items:
        return items
    ts = time.time()
    ts_iso = datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
    for item in items:
        if not isinstance(item, dict):
            continue
        # setdefault:不覆盖已存在的时间戳(给上层可能已经填好的兼容)
        item.setdefault(field, ts)
        item.setdefault(f"{field}_iso", ts_iso)
    return items


def _reverse_concat_and_sort(pages: list[list[dict]], _seen_unused: set = None) -> list[dict]:
    """2026-09-13 新增:把多页 ticks **逆序拼接 + 保险排序** 成时间正序

    pytdx 实测行为(000006.SZ / 20260512 验证):
        start=0    → 首=10:31 末=15:00  (最新成交,收盘前)
        start=2000 → 首=09:15 末=10:31  (较早成交,上午)
        start=4000 → 0 笔                (超出末尾)

    - 每页**内部**已经是时间正序(协议层就这样)
    - 但**页与页之间**是逆序,所以要先拼最后页、再拼前面的页
    - 最后做一次保险排序(防止个别页面内部出现乱序)

    Args:
        pages: 按 page_idx 0..N-1 顺序收集的各页 unique ticks
        _seen_unused: 保留参数,无作用(只为兼容旧调用,保持 return 形状)

    Returns:
        时间正序拼接的 ticks list
    """
    # 1. 逆序拼接:page_idx 大的先拼(上午) → page_idx 小的后拼(下午)
    out: list[dict] = []
    for page in reversed(pages):
        out.extend(page)
    # 2. 保险:按 time 字段排序(HH:MM 字符串天然可比)
    out.sort(key=lambda r: r.get("time", ""))
    return out


def _enrich_ticks(ticks: list, ts_code: str, date: int) -> list[dict]:
    """2026-09-12 新增:给 ticks 列表自动补 ts_code / trade_date / datetime / seqId_in_minute

    字段说明:
        - ts_code              str    "000006.SZ"(直接用入参,所有 tick 一致)
        - trade_date           str    "2026-05-12"
        - datetime             str    "2026-05-12 10:31:00"(time 字段补 ":00")
        - seqId_in_minute      int    同 (ts_code, trade_date, time) 分钟内自增(0, 1, 2, ...)

    入参 ticks 必须是按 time 字段升序的 list;调用方负责排序。
    出参是新 list(不修改入参)。

    不在这里打 data_timestamp(get_orderbook / get_minute_kline 有,get_history_ticks 暂不加)。

    2026-09-19 变更:seqId → seqId_in_minute,含义从"全局当日序号"改为"同分钟内单股序号",
    详见 docs/落库方案_v2.md。旧"全局序号"调用方请用 enumerate 或 DB 自增字段补回。
    """
    if not ticks:
        return []
    trade_date_str = _date_int_to_str(date)
    out = []
    counters: dict[str, int] = {}  # key = time 字符串,值 = 当前分钟内自增序号
    for r in ticks:
        if not isinstance(r, dict):
            r = dict(r)
        # 字段补全(不覆盖原有)
        r["ts_code"] = ts_code
        r["trade_date"] = trade_date_str
        time_str = r.get("time", "")
        if time_str:
            r["datetime"] = _ticks_time_to_datetime(time_str, date)
        # seqId_in_minute:同 time 字段内自增
        r["seqId_in_minute"] = counters.get(time_str, 0)
        counters[time_str] = counters.get(time_str, 0) + 1
        # 删除旧 seqId(避免双字段混淆,2026-09-19 决策)
        r.pop("seqId", None)
        out.append(r)
    return out


# ============ 指数代码(从 tdx_config 重新导出,方便调用方继续 from coreClient.tdx_client import INDEX_CODES) ============

# INDEX_CODES / INDEX_NAMES 已在 tdx_config 定义,这里重新导出
# 业务侧可以直接 from coreClient.tdx_client import INDEX_CODES,或 from coreClient.tdx_config import INDEX_CODES


def is_sealed(quote: dict) -> bool:
    """判断是否封板(ask1=0 且 bid1>0)"""
    if not quote:
        return False
    return (quote.get('ask1', 0) == 0
            and quote.get('bid1', 0) > 0
            and quote.get('bid_vol1', 0) > 0)


def is_broken_seal(quote: dict) -> bool:
    """判断是否炸板(涨停价位但有卖盘)"""
    if not quote:
        return False
    # 当前价 = 涨停价(以 last_close 推算)
    last_close = quote.get('last_close', 0)
    price = quote.get('price', 0)
    if not last_close or not price:
        return False
    # 涨幅 ~10% 且 ask1 > 0
    pct = (price - last_close) / last_close * 100
    return pct > 9.5 and quote.get('ask1', 0) > 0


if __name__ == "__main__":
    # 自检
    client = TdxClient()
    print('connect OK')

    # 测 4 指数
    print('\n--- 4 大指数 5档 ---')
    t0 = time.time()
    quotes = client.get_index_orderbook(INDEX_CODES)
    print(f'  {len(quotes)} 个指数 / {(time.time()-t0)*1000:.0f}ms')
    for q in quotes:
        code = q.get('code', '?')
        market = q.get('market', '?')
        name = INDEX_NAMES.get((market, code), '?')
        print(f'  {name}({code}): {q.get("price")} vol={q.get("vol"):,}')
    client.disconnect()
