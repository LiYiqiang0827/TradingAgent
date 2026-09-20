# docs/落库方案_v2.md 变更记录

## 2026-09-19 00:00(北京时间)
- 初始版本
- 决策:C-a(新建 tbl_tick_v2,旧表保留只读)+ PK-1(`(ts_code, trade_date, time, seqId_in_minute)`)
- 落地步骤 7 条(Step 1-7),按序执行
- 验收标准 6 条,首条即"rows_removed_by_seen 从 195 → 0"
