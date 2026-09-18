# 代码详细设计/service_week.md

`scripts/service/service_week.py` — A 股周 K 重算 service(**派生表,全量覆盖**)。

## 职责

1. **强校验**:`cn_daily` 和 `cn_adj_factor` 的 ctrl 必须一致且 ≥ 今天
2. 实例化 `CNDataDown`
3. 调 `down.update_week()`(无日期参数,全量重算)
4. 写日志到 `logs/service_week.log`

## 入口

```bash
python3 -m service.service_week  # 无日期参数
```

**不接受日期参数**——周 K 是派生表,每次跑都是全量重算。

## 关键流程

```python
def check_daily_adj_consistency(down: CNDataDown) -> bool:
    """校验 tbl_ctrl 中 cn_daily 和 cn_adj_factor 的日期是否一致"""
    today = datetime.now().strftime("%Y%m%d")
    ctrl_daily = get_ctrl(down.conn_basic, "cn_daily")
    ctrl_adj = get_ctrl(down.conn_basic, "cn_adj_factor")
    
    if not ctrl_daily or not ctrl_adj: return False
    if ctrl_daily != ctrl_adj: return False
    if ctrl_daily < today: return False
    return True

def main():
    log_file = PROJECT_ROOT / "logs" / "service_week.log"
    logger.add(str(log_file), rotation="50 MB", level="INFO", enqueue=True)
    
    t0 = time.time()
    try:
        down = CNDataDown()
        if not check_daily_adj_consistency(down):
            logger.error("[service_week] 中止:cn_daily / cn_adj_factor 未就绪")
            return 2  # 业务中止
        n = down.update_week()
        logger.info(f"[service_week] 完成(全量): +{n:,} 行, 用时 {time.time()-t0:.1f}s")
        return 0
    except Exception as e:
        logger.error(f"[service_week] 失败: {e}", exc_info=True)
        return 1
```

## 关键点

- **`check_daily_adj_consistency(down)`** 强校验:
  - `cn_daily` 跟 `cn_adj_factor` 必须都存在
  - 两者的 max_date 必须**完全相等**(YYYYMMDD 字符串比较)
  - 必须**≥ 今天**(确保数据已经包含今天的最新数据)
  - 任一不满足 → return False
- **校验失败 → return 2 业务中止**(scheduler 会捕获 rc=2 并整体 abort 第 3 阶段)
- **`update_week()` 无日期参数**,全量重算
- **log rotation=50 MB**

## 依赖链位置

```
service_daily  →  service_adj_factor  →  service_week
                                          ↓
                                    service_month
```

**`service_week` 跟 `service_month` 都用 `check_daily_adj_consistency`**,逻辑复用。

## 数据流

```
service_week.main()
  ↓
down = CNDataDown()
  ↓
check_daily_adj_consistency(down)
  ↓
  ctrl_daily = get_ctrl(conn_basic, "cn_daily")
  ctrl_adj = get_ctrl(conn_basic, "cn_adj_factor")
  today = datetime.now().strftime("%Y%m%d")
  校验:ctrl_daily == ctrl_adj >= today
  ↓
down.update_week()
  ↓
读 tbl_cn_day + tbl_cn_adj_factor 全表
  ↓
merge + qfq 计算(价 × adj_factor, vol / adj_factor)
  ↓
过滤 open>0 & close>0(新股预占位)
  ↓
_aggregate_daily_to_freq(df_day, freq="weekly")
  ↓
replace_table(conn_basic, df_week, "tbl_cn_week")  # DELETE + INSERT 事务
  ↓
return inserted 行数
```

## 注意事项

- **`update_week` 走 `replace_table`**(覆盖更新,事务化),**会清空原表**
- **scheduler 串行调** `service_week` / `service_month`(代码无 mutex,但 scheduler 第 3 阶段顺序调用保证不并发)
- **`check_daily_adj_consistency` 是 service 间的契约**:必须先有 daily + adj_factor 才能算 week
- **校验失败返回 rc=2**(不是 rc=1),scheduler 知道这是"业务中止"而非异常
