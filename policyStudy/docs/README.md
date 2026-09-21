# policyStudy — 策略研究模块

> 负责策略研究、因子分析、回测、题材研究等。基于已经本地化的行情数据(由 offlineDataManager / onlineDataManager 提供)。

**当前状态**:**🚧 正在建设中**(2026-09-16)

---

## 当前进展

### 已完成部分
- **数据库结构(v3,2026-09-14)**:
  - `policy_day.db` — 日 K(前复权 + 不复权)+ ctrl 表
  - `policy_minute.db` — 分钟 K + ctrl 表
  - `policy_ticks.db` — 逐笔成交 + ctrl 表
  - 都改成"单表 + ctrl 表"结构,横向切片(同一天所有股票)

- **核心脚本**:
  - `scripts/build_policy_db.py` — 把 parquet 导入 SQLite
  - `scripts/policy_db_client.py` — DB 客户端(v3)

- **题材涨停研究**(第一个研究项目):
  - `policy/题材涨停研究/` 下有 watchlist csv(13 MB 历史数据)
  - `scripts/data_gen.py` — 批量数据生成
  - `scripts/watchlist_gen.py` — watchlist 生成器

- **A股复盘工具箱**:
  - `policy/market_review/` — 收盘后全市场事实包、Agent写作包与最终验收
  - 复盘主轴:高标晋级、板块资金迁移、过去主线追踪、首板后首次回调再启
  - 可选导出候选到“题材涨停研究”继续做分钟/逐笔样本研究

### 待完成部分
- 通用因子库(尚未抽象)
- 标准化回测框架(尚未实现)
- 策略评估指标体系(尚未定义)
- 与 offlineDataManager 数据的 join 接口(未做)

---

## 目录结构

```
policyStudy/
├── scripts/                       # 通用工具脚本
│   ├── build_policy_db.py           # parquet → SQLite
│   └── policy_db_client.py          # DB 客户端
├── policy/                         # 具体研究项目
│   └── 题材涨停研究/              # 第一个研究项目
│       ├── scripts/
│       │   ├── data_gen.py          # 批量数据生成
│       │   └── watchlist_gen.py     # watchlist 生成
│       └── watchlist/             # watchlist CSV
├── data/                            # SQLite 数据(被 .gitignore 排除)
│   ├── policy_day.db
│   ├── policy_minute.db
│   └── policy_ticks.db
└── logs/                            # 运行时日志(被 .gitignore 排除)
```

---

## 数据库设计(v3)

**设计原则**(2026-09-14 用户原话):
- 单表 + ctrl 表结构,横向切片(同一天所有股票)
- PK 通常是 `(ts_code, trade_date)` 或 `(ts_code, trade_date, ...)`

### 3 个数据库

| 数据库 | 表 | 主键 | 数据 | 用途 |
|---|---|---|---|---|
| `policy_day.db` | `tbl_day` | (ts_code, trade_date) | 日 K qfq | 前复权日线 |
| | `tbl_day_nofuquan` | (ts_code, trade_date) | 日 K nofuq | 不复权日线 |
| | `tbl_day_ctrl` | (ts_code, trade_date) | YYYYMMDD | qfq 是否已落盘 |
| | `tbl_day_nofuquan_ctrl` | (ts_code, trade_date) | YYYYMMDD | nofq 是否已落盘 |
| `policy_minute.db` | `tbl_minute` | (ts_code, trade_date, time_idx) | 分钟 K | 240 分钟/天 |
| | `tbl_minute_ctrl` | (ts_code, trade_date) | YYYYMMDD | 是否已落盘 |
| `policy_ticks.db` | `tbl_tick` | (ts_code, trade_date, seqId) | 逐笔 | 当日所有 tick |
| | `tbl_tick_ctrl` | (ts_code, trade_date) | YYYYMMDD | 是否已落盘 |

### time_idx 含义(minute 表)

| time_idx 范围 | 时段 |
|---|---|
| 0-119 | 09:31 - 11:30(上午) |
| 120-239 | 13:01 - 15:00(下午) |

---

## 与其他模块的关系

```
offlineDataManager           policyStudy                  policy(具体研究)
  日 K / 周月 K    ──→  ┌──────────────┐    ──→  ┌──────────────┐
  涨停榜 / 题材      ──→ │  policy_day  │    ──→  │  题材涨停研究 │
  资金流向 / 龙虎榜  ──→ │ policy_minute│    ──→  │  ...(更多)     │
                         │ policy_ticks │    ──→  └──────────────┘
                         └──────────────┘
                              ▲
                              │ 数据来源
                              │
onlineDataManager
  实时盘口 / 逐笔  ──→ (TODO: 集成)
```

**当前**:policyStudy 主要从 offlineDataManager 拿数据,**与 onlineDataManager 的实时数据集成待做**。

---

## 上手指南

**读数据**(以日 K 为例):
```python
import sys
sys.path.insert(0, '~/TradingAgent/policyStudy/scripts')
from policy_db_client import PolicyDBClient

client = PolicyDBClient('policy_day.db')
df = client.read_day('000001.SZ', start_date='20260101', end_date='20260915')
print(df.head())
```

**导入新数据**(从 parquet):
```bash
cd ~/TradingAgent/policyStudy/scripts
python3 build_policy_db.py
```

**跑"题材涨停研究"数据生成**:
```bash
cd ~/TradingAgent/policyStudy/policy/题材涨停研究
python3 scripts/data_gen.py watchlist_题材涨停研究_20260908_20260911
```

**跑“A股复盘工具箱”**:
```bash
cd ~/TradingAgent
python -m policyStudy.policy.market_review.build_review_packet \
  --trade-date 20260921 --output-dir /path/to/review_runs/20260921
```

---

## 注意事项

- `data/` 和 `logs/` 已被 .gitignore 排除(数据库和日志不入库)
- 第一次跑某个 watchlist 可能很慢(parquet 解析 + 大批量 INSERT)
- 用 `policy_db_client.py` 的 ctrl 模式避免重跑(增量加载)

---

## 未来规划

1. **统一因子库** — 抽象常用因子(动量 / 波动率 / 量价 / 资金流等)
2. **回测框架** — 向量化回测,支持多因子组合
3. **策略评估** — 标准化指标(Sharpe / MaxDD / Calmar / IC 等)
4. **在线数据集成** — 接入 onlineDataManager 的实时数据做盘中研究
5. **AI 辅助** — 用 LLM 自动生成因子假设和策略

---

## 相关代码与文档

- **代码位置**:`~/TradingAgent/policyStudy/`
- **核心脚本**:
  - `scripts/build_policy_db.py` — 数据导入
  - `scripts/policy_db_client.py` — DB 客户端
- **第一个研究项目**:`policy/题材涨停研究/`
- **复盘工具箱**:`policy/market_review/README.md`

---

## 版本信息

- **v3 数据库改造**:2026-09-14(单表 + ctrl 表)
- **当前版本**:v3 持续演进中
- **新建概览**:2026-09-16
