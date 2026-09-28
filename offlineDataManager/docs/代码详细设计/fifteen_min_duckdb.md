# TDX全市场15分钟OHLC DuckDB

## 数据链路

```text
TDX get_security_bars(KLINE_TYPE_15MIN)
    -> coreClient.tdx_client.TdxClient.download_fifteen_minute()
    -> service/service_fifteenMinute.py
    -> core/offline_db_client.py
    -> core/tdx_fifteen_min_store.py
    -> data/db_fifteenMinute.db (DuckDB)
    -> coreClient.data_provider.get_fifteenMin()
```

旧的移动硬盘淘宝CSV转换库保留在
`data/fifteenMinute/fifteen_min.duckdb`，但统一查询接口不再读取它。

## 时间与价格口径

- TDX原生不复权OHLC、成交量（股）、成交额（元）。
- 每个完整交易日16根：09:45–11:30、13:15–15:00。
- 第一根09:45采用TDX原生口径，包含开盘集合竞价。
- `adj_factor`从本地日复权因子表写入；调用方可按需计算前复权价格。
- 默认从2024-09-01请求，实际最早日期受TDX服务器保留深度限制。

## 表

- `fifteen_min_bars`：15分钟事实表，主键`(ts_code, datetime)`。
- `fifteen_min_days`：逐股票逐日行数与完整性状态。
- `fifteen_min_sync`：逐股票已请求覆盖区间，用于断点续传和每日增量。
- `fifteen_min_meta`：schema版本。

## 使用

```bash
# 单股验证
PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  offlineDataManager/scripts/service/service_fifteenMinute.py \
  --ts-codes 000001.SZ --start-date 20240901 --end-date 20260924

# 全市场首次回补；默认起点20240901，终点为最近收盘交易日
PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  offlineDataManager/scripts/service/service_fifteenMinute.py

# 查看库状态
PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  offlineDataManager/scripts/service/service_fifteenMinute.py --stats-only
```

无参数重复运行会跳过已覆盖区间，只下载新增日期。`--force`用于强制重拉；
`--include-bj`用于额外包含北交所。
