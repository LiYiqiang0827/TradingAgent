# policyStudy — 架构设计文档

> **版本**:v1.0(2026-09-18 重写)
> **代码位置**:`~/TradingAgent/policyStudy/`
> **文档维护**:本目录(`docs/` 软链 → `~/LLM Wiki/TradingAgent/policyStudy/`)

---

## 1. 目录目的与定位

### 1.1 核心目的

`policyStudy/` 是 **TradingAgent 项目的策略研究模块**,职责是**对各种 A 股策略进行系统化研究**,包括:

- 因子分析(动量/波动率/资金流/题材/连板 等)
- 回测框架(向量化回测 + 多因子组合)
- 策略评估(Sharpe/MaxDD/Calmar/IC 等指标)
- Watchlist 编排(动态圈定研究范围)
- 题材/事件研究(涨停板/龙虎榜/复牌 等)
- AI 辅助研究(LLM 自动生成因子假设 + 回测验证)

**面向用户**:策略研究员 / 量化分析师 / 用 LLM 做 A 股研究的 Agent。

### 1.2 不负责的事

policyStudy **不负责**:

- ❌ 行情数据的下载与存储 → 交给 `offlineDataManager`
- ❌ 实时行情的 Redis 流 → 交给 `onlineDataManager`
- ❌ 客户端封装(tushare / kpl / tdx / ths) → 都在 `coreClient/`
- ❌ 直接写 SQL 到本地 db(经 `coreClient.data_provider` 或 `offline_db_client`)

### 1.3 模块依赖关系

```
┌─────────────────────────────────────────────────────────────────┐
│                        policyStudy/                              │
│                                                                   │
│   ┌──────────────────┐         ┌──────────────────┐            │
│   │  题材涨停研究     │         │  <未来策略 1>     │            │
│   │  (第一个研究项目)│         │  <未来策略 2>     │  ...        │
│   └──────────────────┘         └──────────────────┘            │
│            │                              │                       │
│            └──────────────┬───────────────┘                       │
│                           ▼                                       │
│              ┌────────────────────────┐                           │
│              │  coreClient.data_provider│  ← 唯一数据入口         │
│              └────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
   ┌─────────────────────────────────────────────────────────┐
   │              offlineDataManager(数据存储 + 下载)        │
   │  • offline_db_client.py(被动存储层)                    │
   │  • offline_downloader.py(主动下载层)                   │
   │  • scripts/service/(定时同步服务)                     │
   └─────────────────────────────────────────────────────────┘
                           │
                           ▼
   ┌─────────────────────────────────────────────────────────┐
   │              coreClient/(数据源封装)                    │
   │  • tushare_client / kpl_client / tdx_client / ths_client│
   └─────────────────────────────────────────────────────────┘
```

**关键设计**:**policyStudy 的所有数据访问必须经 `coreClient.data_provider`**,**禁止直接调 tushare / kpl / db_client**。这样保证:

- 数据接口可替换(db → online 一键切换)
- 研究代码只关心业务逻辑,不关心数据来源
- 数据 schema 变更只影响 data_provider,不影响研究代码

---

## 2. 数据来源:统一经 `coreClient.data_provider`

### 2.1 为什么必须用 data_provider?

策略研究的特点是:
- 需要频繁对比 **历史(db) + 实时(online)** 数据
- 需要多数据源(tushare + kpl + tdx + ths)拼接
- 不同策略可能用不同时间范围、不同数据源

**`data_provider` 提供 30 个统一的 `get_xxx` 接口**,自动在 db / online 间路由,policyStudy **只用关心业务参数**(传什么 ts_code / trade_date),不用关心数据从哪儿来。

### 2.2 调用示例

```python
import sys
sys.path.insert(0, '~/TradingAgent')

from coreClient.data_provider import (
    get_day, get_basic, get_kpl_list, get_kpl_limit_performance,
    get_minute, get_news,
)

# 1. 默认 db 模式(本地 SQLite)
df_day = get_day(ts_code="000006.SZ", start_date="2026-09-01", end_date="2026-09-18")
df_basic = get_basic(exchange="SSE", market="主板")

# 2. online 模式(实时拉 tushare / kpl)
df_top = get_kpl_list(trade_date="2026-09-17", tags="涨停", source="online")
df_lp = get_kpl_limit_performance(trade_date="2026-09-17", source="online")
# ↑ today 会自动走 KPL 实时接口(fetch_realtime_limit_performance)

# 3. 多源组合(题材研究)
df_limit = get_kpl_list(trade_date="2026-09-17", tags=["涨停", "炸板"], source="online")
df_news = get_news(src=["sina", "cls"], start_date="2026-09-15", end_date="2026-09-17", source="online")

# 4. 个股多周期(因子研究)
df_minute = get_minute(ts_code="000006.SZ", trade_date="2026-09-17", source="database")
df_basic = get_basic(ts_code="000006.SZ")  # 个股基本面
df_cyq = get_cyq_perf(ts_code="000006.SZ", trade_date="2026-09-17")  # 筹码胜率
```

### 2.3 完整接口清单(30 个)

详见 `~/LLM Wiki/TradingAgent/coreClient/data_provider.md` 第 3 章。

### 2.4 严禁的反模式

```python
# ❌ 错误:直接调 tushare_client
from coreClient.tushare_client import TushareClient
df = TushareClient().daily(ts_code='000006.SZ', ...)

# ❌ 错误:直接读 db
import sqlite3
conn = sqlite3.connect('offlineDataManager/data/db_cn_basic.db')
df = pd.read_sql('SELECT * FROM tbl_cn_basic', conn)

# ❌ 错误:直接用 offline_db_client 工具
from offlineDataManager.scripts.core.offline_db_client import get_kpl_list
df = get_kpl_list(...)  # 应该走 data_provider.get_kpl_list

# ✅ 正确:统一经 data_provider
from coreClient.data_provider import get_kpl_list
df = get_kpl_list(trade_date='2026-09-17', source='database')   # db
df = get_kpl_list(trade_date='2026-09-17', source='online')    # online
```

---

## 3. 目录结构

### 3.1 完整目录树

```
~/TradingAgent/policyStudy/
├── docs/                                    # ← 软链到 ~/LLM Wiki/TradingAgent/policyStudy/
│   ├── ARCHITECTURE.md                       # 本文档(架构设计)
│   └── README.md                             # 概览(简化版)
├── logs/                                     # 运行时日志(.gitignore 排除)
│
└── policy/                                   # 策略研究项目集合
    └── 题材涨停研究/                        # 第一个研究项目
        ├── scripts/                          # 该策略的执行脚本
        │   ├── data_gen.py                   # 数据下载/生成入口
        │   └── watchlist_gen.py              # watchlist CSV 生成器
        └── watchlist/                         # watchlist CSV(.gitignore 排除)
            └── watchlist_xxx_*.csv
```

### 3.2 顶层目录说明

| 目录/文件 | 用途 | 是否必须 |
|---|---|---|
| `docs/` | 文档目录(软链到 LLM Wiki) | ✅ 每个项目必备 |
| `logs/` | 运行时生成的日志文件(被 .gitignore 排除) | 可选 |
| `policy/` | 所有研究项目的根目录(每个子目录 = 一个策略) | ✅ 必备 |

### 3.3 策略项目目录的标准结构

**每个策略项目(目录)在 `policy/` 下**,标准结构:

```
policy/<策略名>/
├── scripts/                                  # 该策略的执行脚本(.py)
│   ├── data_gen.py                           # ✅ 必备:数据下载/生成入口
│   └── watchlist_gen.py                      # ✅ 必备:watchlist CSV 生成器
└── watchlist/                                # 该策略的 watchlist CSV 目录(.gitignore 排除)
    └── watchlist_<描述>_<起始日期>_<结束日期>.csv
```

### 3.4 `scripts/data_gen.py` 必填规则

**所有 `policy/<策略名>/scripts/data_gen.py` 必须实现以下接口**:

| 命令 | 作用 | 示例 |
|---|---|---|
| `python3 data_gen.py <watchlist>` | 拉 watchlist 中所有股票指定日期范围的数据 | `python3 data_gen.py watchlist_题材涨停研究_20260908_20260911` |
| `python3 data_gen.py --full` | 全量重新下载(忽略 ctrl 断点) | `python3 data_gen.py --full` |
| `python3 data_gen.py --incremental` | 增量下载(默认,只看 ctrl 缺失的) | `python3 data_gen.py --incremental` |
| `python3 data_gen.py --only minute_index` | 仅拉指数分钟数据 | 多个策略需要指数行情 |

**实现要求**(v3.7 模板):

1. **接收 watchlist 名/路径**:自动在 `watchlist/` 目录下找同名 csv
2. **subprocess 派发到 service**:不自己循环拉,调 `offlineDataManager/scripts/service/service_*` 派发
3. **数据源经 `coreClient.data_provider`**:data_gen 本身**不直接调** tushare/kpl/tdx,而是经 service,而 service 经 data_provider
4. **ctrl 模式**:增量时跳过已下载的 trade_date(用 `tbl_*_ctrl` 表)
5. **错误处理**:缺数据/网络失败不中断整个流程

### 3.5 `scripts/watchlist_gen.py` 必填规则

**所有 `policy/<策略名>/scripts/watchlist_gen.py` 必须实现**:

| 命令 | 作用 |
|---|---|
| `python3 watchlist_gen.py --start-date YYYYMMDD --end-date YYYYMMDD` | 生成日期范围内的 watchlist CSV |

**watchlist CSV 格式**(标准):

```csv
trade_date,ts_code,name,lu_time,lu_desc,status
20260831,000011.SZ,深物业A,09:25:00,地产链,2连板
20260831,000560.SZ,我爱我家,09:25:00,地产链,5天3板
```

**至少包含列**:`trade_date, ts_code`(前两列是核心,后续列是策略特有的元数据)

### 3.6 watchlist CSV 存放规则

- 路径:`policy/<策略名>/watchlist/watchlist_<描述>_<起始日期>_<结束日期>.csv`
- **.gitignore 排除**(`**/watchlist/*.csv`):watchlist 是动态生成的测试数据,不入库
- 大小通常 1-15 MB(取决于日期范围 + 涨停股数量)

### 3.7 文档规范

**每个策略项目根目录**可选放一个 `README.md`(策略说明 + 使用指南),但**架构文档统一放 `policyStudy/docs/`(本目录)**。

---

## 4. 新建策略的标准流程

### 4.1 目录创建

```bash
# 在 policyStudy/policy/ 下新建策略目录
mkdir -p ~/TradingAgent/policyStudy/policy/<策略名>/scripts
mkdir -p ~/TradingAgent/policyStudy/policy/<策略名>/watchlist
```

### 4.2 实现 watchlist_gen.py

```python
# policy/<策略名>/scripts/watchlist_gen.py
"""<策略名> — watchlist 生成器

Usage:
  python3 watchlist_gen.py --start-date 20260101 --end-date 20260831
"""
import argparse
import pandas as pd
from coreClient.data_provider import get_kpl_list  # 数据来源统一


def generate_watchlist(start_date: str, end_date: str) -> pd.DataFrame:
    """生成策略对应的 watchlist"""
    # 示例:每天涨停股
    df = get_kpl_list(
        trade_date=None, start_date=start_date, end_date=end_date,
        tags="涨停", source="database",
    )
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    args = parser.parse_args()

    df = generate_watchlist(args.start_date, args.end_date)
    output_path = f'../watchlist/watchlist_<策略名>_{args.start_date}_{args.end_date}.csv'
    df.to_csv(output_path, index=False)
    print(f'Saved {len(df)} rows to {output_path}')


if __name__ == '__main__':
    main()
```

### 4.3 实现 data_gen.py

```python
# policy/<策略名>/scripts/data_gen.py
"""<策略名> — 数据下载/生成入口

Usage:
  python3 data_gen.py watchlist_<策略名>_<起始>_<结束>  # 拉该 watchlist
  python3 data_gen.py --full                            # 全量
  python3 data_gen.py --only minute_index               # 仅拉指数
"""
import argparse
import subprocess
from pathlib import Path


def run_service(watchlist_path: str):
    """subprocess 派发到 offlineDataManager 的 service"""
    # 派发 service_intraday
    subprocess.run(['python3',
                    '../../offlineDataManager/scripts/service/service_intraday.py',
                    '--watchlist', watchlist_path,
                    '--only', 'intraday'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('watchlist', nargs='?', default=None)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--only', default='minute_index')
    args = parser.parse_args()

    if args.watchlist:
        run_service(f'../watchlist/{args.watchlist}.csv')
    else:
        print('Usage: data_gen.py <watchlist>')


if __name__ == '__main__':
    main()
```

### 4.4 测试

```bash
# 1. 生成 watchlist
cd ~/TradingAgent/policyStudy/policy/<策略名>
python3 scripts/watchlist_gen.py --start-date 20260101 --end-date 20260831

# 2. 跑数据
python3 scripts/data_gen.py watchlist_<策略名>_20260101_20260831

# 3. 验证
python3 -c "
from coreClient.data_provider import get_kpl_list
df = get_kpl_list(trade_date='2026-08-31', source='database')
print(f'loaded {len(df)} rows')
"
```

---

## 5. 当前已实现的策略

### 5.1 `policy/题材涨停研究/`(第一个)

**目的**:研究涨停板的特征、连板规律、封单强度、龙虎榜溢价等。

**scripts/data_gen.py**:subprocess 派发 3 个 service 拉分钟/ticks/指数分钟数据
**scripts/watchlist_gen.py**:生成每日涨停股 watchlist(从 tushare kpl_list 接口)

**详细架构**参见 `~/LLM Wiki/TradingAgent/offlineDataManager/代码详细设计/service_intraday.md` 等 4 篇。

### 5.2 未来策略(待规划)

| 策略名 | 用途 | 优先级 |
|---|---|---|
| `<未来 1>` | TODO | TODO |
| `<未来 2>` | TODO | TODO |

---

## 6. 上手指南

### 6.1 第一次跑某个策略

```bash
cd ~/TradingAgent/policyStudy/policy/<策略名>

# 1. 看策略说明
cat scripts/data_gen.py        # 看支持哪些参数
cat scripts/watchlist_gen.py   # 看支持哪些参数

# 2. 生成 watchlist
python3 scripts/watchlist_gen.py --start-date 20260101 --end-date 20260831

# 3. 拉数据(后台)
nohup python3 scripts/data_gen.py watchlist_<描述>_20260101_20260831 > ../../logs/<策略>_<日期>.log 2>&1 &

# 4. 看进度
tail -f ../../logs/<策略>_<日期>.log

# 5. 验证
python3 -c "
from coreClient.data_provider import get_kpl_limit_performance
df = get_kpl_limit_performance(trade_date='2026-09-17', source='database')
print(df.head())
"
```

### 6.2 常见任务 | 任务 | 命令 |
|---|---|
| 拉某天涨停详情 | `python3 -c "from coreClient.data_provider import get_kpl_limit_performance; print(get_kpl_limit_performance(trade_date='2026-09-17'))"` |
| 看某只股票分钟 K | `python3 -c "from coreClient.data_provider import get_minute; print(get_minute(ts_code='000006.SZ', trade_date='2026-09-17'))"` |
| 看龙虎榜某天 | `python3 -c "from coreClient.data_provider import get_top_list; print(get_top_list(trade_date='2026-09-17'))"` |
| 看融资融券 | `python3 -c "from coreClient.data_provider import get_margin_detail; print(get_margin_detail(ts_code='000006.SZ', trade_date='2026-09-17'))"` |

### 6.3 调试常见问题

| 症状 | 原因 | 解决 |
|---|---|---|
| `ModuleNotFoundError: No module named 'coreClient'` | PYTHONPATH 没设 | `export PYTHONPATH=~/TradingAgent:$PYTHONPATH` |
| `ModuleNotFoundError: No module named 'offlineDataManager'` | 同上 | 同上 |
| `data_provider.get_kpl_limit_performance(trade_date=today)` 返 0 行 | 用了 historical 接口,盘中 today 必须走实时接口 | data_provider 已自动处理:今天 → `fetch_realtime_limit_performance()`,历史 → `get_daily_limit_performance()` |
| `tushare 限流` | 30 次/分钟 | 让 service 慢慢跑,等 60s |

---

## 7. 与其他模块的边界

| 模块 | 给 policyStudy 提供 | policyStudy 给它们提供 |
|---|---|---|
| `coreClient/` | 数据接口(`data_provider`)+ 客户端封装 | (无,policyStudy 是叶子端) |
| `offlineDataManager/` | 数据存储 + service 派发 | (无) |
| `onlineDataManager/` | 实时行情 + Redis 流 | 策略输出(待集成) |
| `monitor/` | (未来)策略信号推送 | 监控结果 |
| `quant-strategy/` | (未来)实盘对接 | 策略信号 |

---

## 8. 设计原则(2026-09-18 拍板)

1. **数据统一从 `coreClient.data_provider` 来**
   - 禁止直接调 `tushare_client` / `kpl_client` / `tdx_client`
   - 禁止直接读 db(sqlite3)
   - 禁止直接用 `offline_db_client`(除非 data_provider 没暴露的内部函数)

2. **每个策略项目必须有 `scripts/data_gen.py` + `scripts/watchlist_gen.py`**
   - data_gen 派发 service,不自己循环拉
   - watchlist_gen 生成 csv 标准格式

3. **watchlist CSV 是策略产物,不入库**(`.gitignore` 已排除)

4. **service 在 offlineDataManager 下**(不在 policyStudy 下)
   - policyStudy 不维护数据下载逻辑
   - service 由 `offlineDataManager` 统一管理 cron + 依赖

5. **db 物理文件在 `offlineDataManager/data/` 下**(不在 policyStudy/data/)
   - policyStudy 不直接管 db 文件

6. **架构文档统一在 `policyStudy/docs/`**(LLM Wiki)
   - 每个策略项目**不单独**写架构文档(避免分裂)
   - 策略特有说明写在策略根目录的 `README.md`

---

## 9. 变更历史

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-15 | v0.1 | 初创 `policy_db_client.py` + `build_policy_db.py` |
| 2026-09-17 | v0.5 | 重构:`policy_db_client` 合并到 `offline_db_client`;3 个 service 加到 `offlineDataManager`;data_gen v3.7 subprocess 派发 |
| 2026-09-18 | v1.0 | **重写本文档**:统一数据接口(`coreClient.data_provider`);规范每个策略目录结构(`scripts/data_gen.py` + `scripts/watchlist_gen.py`);删除原 ARCHITECTURE.md 旧版 |

---

## 10. 相关文档

| 文档 | 路径 | 作用 |
|---|---|---|
| `coreClient.data_provider.md` | `~/LLM Wiki/TradingAgent/coreClient/data_provider.md` | **30 个数据接口详细文档**(必读) |
| `coreClient.doc_index` | `~/LLM Wiki/TradingAgent/coreClient/doc_index.md` | coreClient 总览 |
| `service_intraday.md` | `~/LLM Wiki/TradingAgent/offlineDataManager/代码详细设计/service_intraday.md` | 分钟 K service 设计 |
| `service_ticks.md` | `~/LLM Wiki/TradingAgent/offlineDataManager/代码详细设计/service_ticks.md` | 逐笔 service 设计 |
| `service_intraday_index.md` | `~/LLM Wiki/TradingAgent/offlineDataManager/代码详细设计/service_intraday_index.md` | 指数分钟 service 设计 |