# 代码详细设计/service_margin.md

`scripts/service/service_margin.py` — 融资融券交易汇总 service(2026-09-15 新增)。

## 职责

1. 解析 `--start-date` / `--end-date`
2. 调 `resolve_date_range` 决定拉取范围
3. 调 `down.update_margin(start_date, end_date)`
4. 写日志到 `logs/service_margin.log`

## 入口

```bash
python3 -m service.service_margin  # 增量
python3 -m service.service_margin --start-date 20100401 --end-date 20100430
```

## 关键点

- **CTRL_KEY = "cn_margin"**
- **`update_margin` 写 `tbl_cn_margin`**(basic DB)
- **断点**:`tbl_basic_ctrl.cn_margin`
- **按日循环**(无 range 参数,必须单日)
- 跳过周末(融资融券仅交易日)
- 单日 3 行(SSE + SZSE + BSE)
- **用户要求:挂 task_morning_update(09:05)**

## 依赖链位置

```
scheduler 触发时间表:
  - 09:05  task_morning_update → service_margin
```

**只挂一个 task**(2026-09-15):`task_morning_update` 阶段

## 数据流

```
service_margin.main()
  ↓
resolve_date_range(conn_basic, "cn_margin", args)
  ↓
down.update_margin(start_date, end_date)
  ↓
按日循环(跳过周末)
  对每天:
    df = self.client.margin(trade_date=td)  # 3 行
    if df is not None and len(df) > 0:
      upsert_df(conn_basic, df, "tbl_cn_margin", key_cols=["trade_date", "exchange_id"])
  ↓
update_ctrl(conn_basic, "cn_margin", last_success_date)
```

## 注意事项

- **单日最多 3 行**(SSE + SZSE + BSE)
- **BSE 2021 才开业**,2021 之前只有 2 行(SSE + SZSE)
- **实际起始 2015-01-05**(用户要求 2015-01-01 开始,实测 2015-01-05 最早有数据)
- 融资融券数据盘后才出,挂 task_morning 抓"昨天"
- 无 range 参数,必须按日循环

## 性能数据(2026-09-15 实测)

| 场景 | 数据量 | 耗时 |
|---|---|---|
| 单日 | 3 行 | 0.4s,1 次调用 |
| 全量 2015-01-05 ~ 2026-09-15(~12 年) | ~1.1 万行 | ~5-7 分钟 |
