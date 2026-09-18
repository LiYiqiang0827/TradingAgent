# 代码详细设计/service_margin_detail.md

`scripts/service/service_margin_detail.py` — 融资融券交易明细 service(2026-09-15 新增)。

## 职责

1. 解析 `--start-date` / `--end-date` / `--trade-date`
2. 调 `resolve_date_range` 决定拉取范围
3. 调 `down.update_margin_detail(start_date, end_date)`
4. 写日志到 `logs/service_margin_detail.log`

## 入口

```bash
python3 -m service.service_margin_detail  # 增量
python3 -m service.service_margin_detail --start-date 20171229 --end-date 20180131
python3 -m service.service_margin_detail --trade-date 20240913  # 单日
```

## 关键点

- **CTRL_KEY = "cn_margin_detail"**
- **`update_margin_detail` 写 `tbl_cn_margin_detail`**(basic DB)
- **断点**:`tbl_basic_ctrl.cn_margin_detail`
- **按月循环 + OFFSET 分页**(单月 2000-6000 行,需 OFFSET 0-1 次)
- 主键:`(trade_date, ts_code)`
- **用户要求:挂 task_morning_update(09:05)**(跟 `service_margin` 一起)

## 依赖链位置

```
scheduler 触发时间表:
  - 09:05  task_morning_update → service_margin_detail
```

**只挂一个 task**(2026-09-15):`task_morning_update` 阶段

## 数据流

```
service_margin_detail.main()
  ↓
resolve_date_range(conn_basic, "cn_margin_detail", args)
  ↓
down.update_margin_detail(start_date, end_date)
  ↓
按月循环:
  对每月:
    offset = 0
    while True:
      df = self.client.margin_detail(
        start_date=cur_sd, end_date=cur_ed, limit=6000, offset=offset,
      )
      if df is None or len(df) == 0: break
      upsert_df(conn_basic, df, "tbl_cn_margin_detail", key_cols=["trade_date", "ts_code"])
      if len(df) < 6000: break  # 拉完了
      offset += 6000
      if offset > 100000: break  # 安全上限
  ↓
update_ctrl(conn_basic, "cn_margin_detail", last_success_date)
```

## 注意事项

- **单次最多 6000 条**(Tushare 限制)
- **单月 2000-6000 行**(2018 早期约 1000,2024 约 4000)
- **OFFSET 分页**: 单月可能 1-2 页
- **实际起始 2014-12-31**(用户要求 2015-01-01 开始,实测 2014-12-31 最早有数据)
- 融资融券数据盘后才出,挂 task_morning 抓"昨天"

## 性能数据(2026-09-15 实测)

| 场景 | 数据量 | 耗时 |
|---|---|---|
| 单日 | 3923 行 | 0.5s,1 次调用 |
| 单月 (2024-09) | ~80,000 行 | ~2s,1 次调用(不满 6000) |
| 全量 2014-12-31 ~ 2026-09-15(141 个月) | ~1100 万行 | ~30-45 分钟 |