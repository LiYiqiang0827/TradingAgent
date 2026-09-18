# AI Agent Start — TradingAgent 项目速读

> **目标读者**:刚接触 TradingAgent 项目的 AI Agent(任何 LLM,任何任务)
> **阅读时间**:5 分钟
> **作用**:快速理解项目是什么 / 怎么组织的 / 从哪看文档 / 哪些必读

---

## 1. 这个项目是什么?

**TradingAgent** 是 A 股**量化研究 + 数据基础设施**项目,由 4 个子模块组成:

| 子模块 | 一句话 |
|---|---|
| **coreClient** | 所有外部数据源(tushare / kpl / tdx / ths / redis)的客户端封装 + **统一入口 `data_provider`** |
| **offlineDataManager** | **离线数据**下载 + 落盘到 SQLite(每天定时跑) |
| **onlineDataManager** | **实时数据**Redis 流(全天跑) |
| **policyStudy** | **策略研究**(涨停 / 因子 / 回测),**只读**上面 3 个模块的数据 |

**核心数据规模**(2026-09-18):
- 5 个 SQLite / 30 数据表 / 21 GB
- `coreClient.data_provider` 暴露 **30 个统一 `get_xxx` 接口**,覆盖 db 全部 30 个真数据表
- 199 个测试断言,100% 通过

---

## 2. 项目结构

```
TradingAgent/
├── coreClient/           # ⚠️ 必读入口
├── onlineDataManager/     # 实时(Redis 流)
├── offlineDataManager/    # 离线(SQLite + 调度)
└── policyStudy/           # 策略研究
```

---

## 3. 文档入口(按重要性排序)

### 🔴 必读(项目核心)

| 文档 | 路径 | 必读理由 |
|---|---|---|
| **`coreClient/data_provider.md`** | `~/LLM Wiki/TradingAgent/coreClient/data_provider.md` | **30 个数据接口** — 上层 Agent 唯一数据入口。所有研究/分析任务都用它。 |
| `coreClient/doc_index.md` | `~/LLM Wiki/TradingAgent/coreClient/doc_index.md` | coreClient 总览(客户端索引) |
| `policyStudy/ARCHITECTURE.md` | `~/LLM Wiki/TradingAgent/policyStudy/ARCHITECTURE.md` | 策略研究架构 — 数据访问铁律,新建策略的流程 |

### 🟡 看任务再看

| 文档 | 路径 | 何时看 |
|---|---|---|
| `TradingAgent项目概览.md` | `~/LLM Wiki/TradingAgent/TradingAgent项目概览.md` | 项目整体(本 START 文档的完整版) |
| `offlineDataManager/README.md` | `~/LLM Wiki/TradingAgent/offlineDataManager/README.md` | 下载新数据 / 加新数据源 |
| `offlineDataManager/QuickStart.md` | 同上 | 5 分钟上手 offlineDataManager |
| `offlineDataManager/02_新增数据接入指南.md` | 同上 | 加新数据流程 |
| `offlineDataManager/01_数据详细设计文档.md` | 同上 | db schema(5 DB / 37 表) |
| `onlineDataManager/04_QuickStart.md` | `~/LLM Wiki/TradingAgent/onlineDataManager/04_QuickStart.md` | 实时数据 |

### 🟢 代码文档(API 细节)

| 文档 | 路径 |
|---|---|
| `coreClient/tushare_client.md` | tushare 21 个方法 |
| `coreClient/kpl_client.md` | 同花顺 HTTP API |
| `coreClient/tdx_client.md` | 通达信 pytdx |
| `coreClient/ths_client.md` | 同花顺客户端 |
| `coreClient/redis_client.md` | Redis 基础层 |
| `offlineDataManager/代码详细设计/` | 40+ 个脚本级详细设计 |

---

## 4. ⚠️ 关键约束(违反会出错)

### 数据访问铁律(2026-09-18 拍板)

| ❌ 严禁 | ✅ 正确做法 |
|---|---|
| `from coreClient.tushare_client import TushareClient` 直接调 | `from coreClient.data_provider import get_xxx` |
| `import sqlite3` 直接读 db | 用 `data_provider.get_xxx(source="database")` |
| `from offlineDataManager.scripts.core.offline_db_client import ...` | 同上,**必须经 data_provider** |

**所有上层 Agent(策略 / 监控 / UI)** 一律用 `coreClient.data_provider`,**不要碰底层 client**。

### 看代码的入口

| 任务 | 入口 |
|---|---|
| 拉某只股票日 K | `from coreClient.data_provider import get_day` |
| 拉涨停表现 | `from coreClient.data_provider import get_kpl_limit_performance` |
| 拉分钟 K | `from coreClient.data_provider import get_minute` |
| 拉新闻 | `from coreClient.data_provider import get_news` |

完整 30 个接口见 **`coreClient/data_provider.md`**。

---

## 5. 5 分钟快速开始

```python
import sys
sys.path.insert(0, '/Users/nickzhang/TradingAgent')

from coreClient.data_provider import (
    get_day, get_basic, get_kpl_limit_performance, get_news,
)

# 默认 db 模式(本地 SQLite,快)
df = get_day(ts_code="000006.SZ", start_date="2026-09-01", end_date="2026-09-18")
print(f"日 K: {len(df)} 行")

# online 模式(走 tushare / tdx / kpl,慢但实时)
df = get_kpl_limit_performance(trade_date="2026-09-17", source="online")
print(f"涨停详情: {len(df)} 行")

# db 模式 fallback(没数据时自动调 online)
df = get_kpl_list(trade_date="2026-09-17", tags="涨停", source="database")
print(f"涨停榜: {len(df)} 行")
```

---

## 6. 上手指南(任务驱动)

| 你的任务 | 第一步 |
|---|---|
| 我要做因子研究 | 看 `policyStudy/ARCHITECTURE.md` §3(目录结构)+ §4(新建流程) |
| 我要加新数据源 | 看 `offlineDataManager/02_新增数据接入指南.md` |
| 我要下载新数据 | 看 `offlineDataManager/QuickStart.md` |
| 我要看实时盘口 | 看 `onlineDataManager/04_QuickStart.md` |
| 我要加新 client | 看 `coreClient/doc_index.md` 现有 5 个 client 的设计模式 |
| 我要写新策略 | 看 `policyStudy/ARCHITECTURE.md` §3.4(data_gen.py 模板) |

---

## 7. 仓库地址

- **代码**:`git@github.com:LiYiqiang0827/TradingAgent.git`
- **Wiki(本文档)**:`git@github.com:LiYiqiang0827/wiki-TradingAgent.git`
- **两个仓库独立维护**,Wiki 在 `~/LLM Wiki/TradingAgent/`(LLM Wiki 是独立 git 仓库)

---

## 8. 同步说明

本文档位于:
- **`~/LLM Wiki/TradingAgent/AI_AGENT_START.md`**(主版本)
- **`~/TradingAgent/AI_AGENT_START.md`**(同步副本,方便代码目录直接看)

两边内容一致,只在 LLM Wiki 仓库修改,手动 `cp` 到 `~/TradingAgent/`。

---

## 变更历史

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-18 | v1.0 | 初版 — AI Agent 速读项目 |