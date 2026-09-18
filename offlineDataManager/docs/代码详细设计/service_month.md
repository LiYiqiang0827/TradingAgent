# 代码详细设计/service_month.md

`scripts/service/service_month.py` — A 股月 K 重算 service(**派生表,全量覆盖**)。

## 职责

1. **强校验**(复用 `service_week.check_daily_adj_consistency`)
2. 调 `down.update_month()`(无日期参数,全量重算)
3. 写日志到 `logs/service_month.log`

## 入口

```bash
python3 -m service.service_month  # 无日期参数
```

## 关键流程

```python
from service.service_week import check_daily_adj_consistency  # 复用

def main():
    log_file = PROJECT_ROOT / "logs" / "service_month.log"
    logger.add(str(log_file), rotation="50 MB", level="INFO", enqueue=True)
    
    t0 = time.time()
    try:
        down = CNDataDown()
        if not check_daily_adj_consistency(down):
            logger.error("[service_month] 中止:cn_daily / cn_adj_factor 未就绪")
            return 2  # 业务中止
        n = down.update_month()
        logger.info(f"[service_month] 完成(全量): +{n:,} 行, 用时 {time.time()-t0:.1f}s")
        return 0
    except Exception as e:
        logger.error(f"[service_month] 失败: {e}", exc_info=True)
        return 1
```

## 关键点

- **复用 `service_week.check_daily_adj_consistency`**:从 `service.service_week` import
- **校验失败 → return 2**
- **`update_month()` 无日期参数**,全量重算
- **log rotation=50 MB**

## 依赖链位置

```
service_daily  →  service_adj_factor  →  service_week
                                          ↓
                                    service_month
```

`service_month` 是依赖链最末端。

## 数据流

```
service_month.main()
  ↓
down = CNDataDown()
  ↓
check_daily_adj_consistency(down)  # 复用 service_week 的校验
  ↓
down.update_month()
  ↓
读 tbl_cn_day + tbl_cn_adj_factor 全表
  ↓
merge + qfq 计算
  ↓
过滤 open>0 & close>0
  ↓
_aggregate_daily_to_freq(df_day, freq="monthly")
  _group = trade_date[:6]  # YYYYMM
  ↓
replace_table(conn_basic, df_month, "tbl_cn_month")
  ↓
return inserted
```

## 注意事项

- **`update_month` 跟 `update_week` 区别只在 `freq="monthly"` vs `freq="weekly"`**
- **聚合粒度**:monthly 按 YYYYMM 自然月,weekly 按 ISO year-week
- **trade_date**:monthly 是月内最后交易日,weekly 是周内最后交易日
- **`check_daily_adj_consistency` 不能并行调用**:`service_week` / `service_month` 必须串行(已在 scheduler 第 3 阶段顺序)
