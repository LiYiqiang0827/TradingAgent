# TradingAgent

> **🚨 AI Agent(任何 LLM)必读**:**先读 [`docs/AI_AGENT_START.md`](./docs/AI_AGENT_START.md)**(5 分钟快速理解项目 + 数据访问铁律)。

A 股金融数据本地化与策略研究系统。

---

## 1. 目录结构

```
TradingAgent/
├── coreClient/               # 通用 API 客户端(tushare / kpl / tdx / ths / redis)
│   └── docs/                 # 7 个文档,⚠️ 必读 data_provider.md
│
├── offlineDataManager/        # 离线数据下载与本地化(SQLite + service)
│   └── docs/                 # 48 个文档
│
├── onlineDataManager/         # 在线数据服务(Redis 流)
│   └── docs/                 # 57 个文档
│
├── policyStudy/               # 策略研究(A 股因子 / 回测)
│   └── docs/                 # 2 个文档(⚠️ 必读 ARCHITECTURE.md)
│
├── docs/                      # ⭐ 项目级文档(AI Agent 入口)
│   ├── AI_AGENT_START.md     # ⭐⭐⭐ 必读(5 分钟,任何 LLM 都先看这)
│   └── TradingAgent项目概览.md
│
└── README.md                 # 本文件(给你点提示)
```

---

## 2. 📖 文档阅读顺序(新 Agent 入手)

1. **⭐⭐⭐ [`docs/AI_AGENT_START.md`](./docs/AI_AGENT_START.md)** ← **从这开始**(5 分钟)
2. ⭐⭐ [`docs/TradingAgent项目概览.md`](./docs/TradingAgent项目概览.md) — 项目整体 + 4 子模块关系
3. ⭐ [`coreClient/docs/data_provider.md`](./coreClient/docs/data_provider.md) — 30 个数据接口(上层唯一入口)
4. ⭐ [`policyStudy/docs/ARCHITECTURE.md`](./policyStudy/docs/ARCHITECTURE.md) — 策略研究架构(必读 §4 数据访问铁律)

按需要看:
- 想下载新数据? → `offlineDataManager/docs/02_新增数据接入指南.md`
- 想抓实时数据? → `onlineDataManager/docs/`
- 想研究策略? → `policyStudy/docs/ARCHITECTURE.md`

---

## 3. ⚠️ 数据访问铁律(2026-09-18 拍板)

**所有上层 Agent(策略 / 监控 / UI)一律用 `coreClient.data_provider`,严禁绕过**:

```python
# ❌ 严禁
from coreClient.tushare_client import TushareClient
import sqlite3
from offlineDataManager.scripts.core.offline_db_client import ...

# ✅ 正确
from coreClient.data_provider import get_day, get_kpl_limit_performance, get_news
df = get_day(ts_code="000006.SZ", start_date="2026-09-01", end_date="2026-09-18")
```

**30 个统一接口**:`get_minute / get_minute_index / get_ticks / get_day / get_week / get_month / get_basic / get_adj_factor / get_stk_limit / get_daily_basic / get_moneyflow / get_index_basic / get_index_daily / get_tradecal / get_kpl_list / get_kpl_concept_cons / get_kpl_limit_performance / get_news / get_major_news / get_cctv_news / get_suspend / get_top_list / get_top_inst / get_block_trade / get_ggt_daily / get_hsgt_top10 / get_limit_list / get_margin / get_margin_detail / get_cyq_perf`

详细见 [`coreClient/docs/data_provider.md`](./coreClient/docs/data_provider.md)。

---

## 4. 快速开始

```python
import sys
sys.path.insert(0, '/Users/nickzhang/TradingAgent')

from coreClient.data_provider import (
    get_day, get_kpl_limit_performance, get_news,
)

# 默认 db 模式(本地 SQLite,快)
df = get_day(ts_code="000006.SZ", start_date="2026-09-01", end_date="2026-09-18")
print(f"日 K: {len(df)} 行")

# online 模式(tushare / kpl 实时)
df = get_kpl_limit_performance(trade_date="2026-09-17", source="online")
print(f"涨停详情: {len(df)} 行")
```

---

## 5. 数据规模(2026-09-18 实测)

- **5 个 SQLite 库** / **30 数据表 + 7 ctrl 断点表**
- **30 个统一数据接口**(`coreClient.data_provider`)
- **199 个测试断言**(100% 通过)
- **21 GB** 主仓数据库

---

## 6. 仓库地址

- **代码 + 文档**:`git@github.com:LiYiqiang0827/TradingAgent.git`(本仓库,单一)
- 历史:文档原在 `~/LLM Wiki/TradingAgent/` 独立 git 仓库,2026-09-18 迁移并改为软链(`~/LLM Wiki/TradingAgent -> ~/TradingAgent`),**只维护一个仓库**

---

## 7. 隐私

本仓库**不包含**:
- 数据库文件(`data/` 目录)
- 运行时日志(`logs/` 目录)
- API token / 密钥

clone 后根据各子项目文档配置 token。

---

## 8. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-09-15 | 初版 README |
| 2026-09-18 | **重写 README**:加 ⚠️ 必读 [`docs/AI_AGENT_START.md`](./docs/AI_AGENT_START.md) 提示;加数据访问铁律;更新文档阅读顺序;数据规模更新到 30 接口 / 199 断言 |