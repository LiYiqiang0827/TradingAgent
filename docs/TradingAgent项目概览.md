# TradingAgent 项目概览

> **版本**:v2.0(2026-09-18 重写)
> **项目顶层入口文档** — 介绍 TradingAgent 的 4 个子模块各自的职责、技术栈和文档入口。

---

## 项目结构

```
TradingAgent/
├── coreClient/               # 基础与通用数据服务(API 客户端 + data_provider 统一入口)
├── onlineDataManager/         # 实时市场数据(在线服务,Redis 流)
├── offlineDataManager/        # 离线市场数据(下载 + 本地化,SQLite)
└── policyStudy/               # 策略研究(A 股因子 / 回测 / 题材研究)
```

**4 个子模块的职责边界**:
- **coreClient**:所有外部数据源的客户端封装 + 统一入口 `data_provider`(上层只用这个)
- **offlineDataManager**:所有**离线数据**的存储 + 下载 + 调度(`tbl_*_ctrl` 断点表)
- **onlineDataManager**:**实时数据**的 Redis 流 + 盘口 / 快照
- **policyStudy**:策略研究(因子 / 回测 / 题材)**只读**上面 3 个模块的数据

---

## 1. coreClient — 基础与通用数据服务

**职责**:封装各种外部数据源的 API 客户端,提供**统一入口 `data_provider`** 供上层模块调用。

### 1.1 客户端(5 个)

| 客户端 | 数据源 | 用途 |
|---|---|---|
| **TushareClient** | Tushare Pro API | A 股日 K / 复权因子 / 财务 / 指数等 |
| **KPLClient** | 同花顺 HTTP API(开盘啦) | 涨停榜 / 涨停表现 / 龙虎榜 / 题材 |
| **RedisClient** | Redis | 实时行情缓存 / 队列 |
| **TdxClient** | 通达信 pytdx | 分钟 K / 实时 5 档行情 |
| **THSClient** | 同花顺客户端 | Monitor 实时监控 |

### 1.2 统一入口:`data_provider.py`(2026-09-17 新增,2026-09-18 扩展到 30 接口)

**所有上层模块的数据访问必须经 `coreClient.data_provider`**(严禁直接调 tushare_client / kpl_client / 读 sqlite3):

```python
from coreClient.data_provider import get_day, get_kpl_list, get_minute

# 默认 db 模式(本地 SQLite)
df = get_day(ts_code="000006.SZ", start_date="2026-09-01", end_date="2026-09-18")

# online 模式(走对应 client)
df = get_kpl_list(trade_date="2026-09-17", source="online")
```

**30 个统一接口**(覆盖 db 全部 30 个真数据表):

| 类别 | 接口 |
|---|---|
| 高频 K | get_minute / get_minute_index / get_ticks |
| K 线 | get_day / get_week / get_month |
| 档案/复权 | get_basic / get_adj_factor / get_stk_limit / get_daily_basic / get_moneyflow |
| 指数 | get_index_basic / get_index_daily |
| 交易日历 | get_tradecal |
| KPL | get_kpl_list / get_kpl_concept_cons / get_kpl_limit_performance |
| 新闻 | get_news / get_major_news / get_cctv_news |
| 扩展 | get_suspend / get_top_list / get_top_inst / get_block_trade / get_ggt_daily / get_hsgt_top10 / get_limit_list / get_margin / get_margin_detail / get_cyq_perf |

**位置**:`~/TradingAgent/coreClient/`

**文档入口**:`~/LLM Wiki/TradingAgent/coreClient/`
- **`data_provider.md`** — 30 个接口详细文档(必读)
- `doc_index.md` — coreClient 总览
- `tushare_client.md` — Tushare 21 个方法
- `kpl_client.md` — 同花顺 HTTP API
- `redis_client.md` — Redis 基础层
- `tdx_client.md` — 通达信 pytdx
- `ths_client.md` — 同花顺客户端

---

## 2. onlineDataManager — 实时市场数据更新与接口

**职责**:持续抓取实时市场数据(行情快照、5 档盘口、分钟 K),落盘到 Redis / SQLite,并对外提供查询接口。

**核心服务**(27 个):
- 实时行情快照
- 5 档盘口 / 逐笔成交
- 分钟 K 线
- 集合竞价数据
- 异动监控

**位置**:`~/TradingAgent/onlineDataManager/`

**文档入口**:`~/LLM Wiki/TradingAgent/onlineDataManager/`
- `01_架构文档.md` — 系统架构
- `02_代码详细设计.md` — 代码层设计
- `03_数据详细设计.md` — 数据模型
- `04_QuickStart.md` — 上手指南
- `05_新增实时监控落盘数据指南.md` — 加新监控数据
- `06_Service_启动时间表与周期.md` — service 调度

---

## 3. offlineDataManager — 离线市场数据更新与接口

**职责**:定期从 Tushare / 同花顺拉取离线行情数据(日 K / 复权因子 / 涨停榜 / 财务 / 新闻等),落盘到 SQLite,提供只读查询接口。

**数据库**:**5 个 SQLite**(5 DB / 30 数据表 + 7 ctrl 断点表):

| 数据库 | 表数 | 用途 |
|---|---|---|
| `db_cn_basic.db` | 14 | 股票基本信息 + 日/周/月 K + 涨跌停 + 财务 + 复权 + 资金流 + 融资融券 + 筹码 |
| `db_cn_kpl.db` | 8 | KPL 涨停榜 + 题材 + 龙虎榜 + 大宗交易 + 沪深港通 |
| `db_cn_index.db` | 2 | 指数基本信息 + 指数日线 |
| `db_cn_news.db` | 3 | 普通新闻 + 头条 + CCTV |
| `policy_minute.db / policy_ticks.db` | 4 | 分钟 K + 逐笔成交(policy 专用) |

**核心 service**(2026-09-17 重构后,统一调度):

| Service | 数据源 | 用途 |
|---|---|---|
| `service_intraday.py` | tdx | 个股分钟 K(从 watchlist 下载) |
| `service_ticks.py` | tdx | 个股分笔成交 |
| `service_intraday_index.py` | tdx | 5 大盘指数分钟 K(000001.SH / 399001.SZ / 399006.SZ / 000688.SH / 000016.SH) |
| `service_basic.py` | tushare | 股票基础信息 |
| `service_daily_basic.py` | tushare | 每日指标 |
| `service_moneyflow.py` | tushare | 资金流向 |
| `service_cyq_perf.py` | tushare | 筹码胜率 |
| `service_kpl_limit_performance.py` | kpl | KPL 涨停表现(盘中走实时接口) |
| `service_*.py` | 各种 | 详见 `service_*.md` |

**位置**:`~/TradingAgent/offlineDataManager/`

**文档入口**:`~/LLM Wiki/TradingAgent/offlineDataManager/`
- `README.md` — 总览
- `00_架构文档.md` — 系统架构
- `01_数据详细设计文档.md` — 5 库 37 表 schema
- `02_新增数据接入指南.md` — 加新数据流程
- `03_调度时间表.md` — 调度时间表
- `QuickStart.md` — 5 分钟上手
- `代码详细设计/` — 40+ 个脚本级详细设计
- `修改记录/` — 文档变更历史

---

## 4. policyStudy — 策略研究

**职责**:基于已经本地化的行情数据,**做 A 股策略研究**(因子分析 / 题材轮动 / 连板研究 / 回测)。

### 4.1 当前进展(2026-09-18)

- ✅ **架构文档重写完成**(`policyStudy/ARCHITECTURE.md`,v1.0,19.5KB)
- ✅ **数据来源统一经 `coreClient.data_provider`**(2026-09-18 拍板)
- ✅ **第一个研究项目**:`policy/题材涨停研究/`(涨停板特征 / 连板规律 / 封单强度 / 龙虎榜溢价)
- 🚧 通用因子库(尚未抽象)
- 🚧 标准化回测框架(尚未实现)

### 4.2 标准策略项目结构

```
policyStudy/policy/<策略名>/
├── scripts/
│   ├── data_gen.py           # ✅ 必备:数据下载/生成入口(subprocess 派发 service)
│   └── watchlist_gen.py      # ✅ 必备:watchlist CSV 生成器
└── watchlist/                # watchlist CSV(.gitignore 排除)
```

**关键约束**(ARCHITECTURE.md 拍板):
- 严禁直接调 `tushare_client` / `kpl_client` / `tdx_client`
- 严禁直接读 sqlite3 db
- 严禁直接用 `offline_db_client`(必须经 `coreClient.data_provider`)
- 每个策略项目**必备** `scripts/data_gen.py` + `scripts/watchlist_gen.py`

**位置**:`~/TradingAgent/policyStudy/`

**文档入口**:`~/LLM Wiki/TradingAgent/policyStudy/`
- **`ARCHITECTURE.md`** — 10 章架构设计(必读,2026-09-18 重写)
- `README.md` — 概览

---

## 模块间协作关系

```
                  ┌──────────────────┐
                  │   coreClient     │
                  │  ┌────────────┐  │
                  │  │data_provider│ ←── 统一入口(所有上层只调它)
                  │  └─────┬──────┘  │
                  │  ┌─────┴──────────────────────────┐  │
                  │  │ Tushare/KPL/Redis/Tdx/THS Clients│  │
                  │  └───────────────────────────────┘  │
                  └──────┬─────────────────────────────┘
                         │ 提供 API
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│offlineData   │ │onlineData    │ │ policyStudy  │
│Manager       │ │Manager       │ │              │
│              │ │              │ │              │
│ • 30 数据表  │ │ • Redis 缓存 │ │ • 因子研究   │
│ • 7 service  │ │ • 实时盘口   │ │ • 涨停研究   │
│ • 4 SQLite   │ │ ← online     │ │ ← db + online│
│  → 5 DB      │ │              │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │ 提供离线数据 │ 提供实时数据 │ 基于行情分析
       │ (db 模式)     │ (online)    │
       └───────────────┴───────────────┘
              │
              ▼
     ┌──────────────────┐
     │   上层应用        │
     │   (策略 / UI)     │
     └──────────────────┘
```

**关键数据流**:

1. **离线下载**:`tushare / kpl / tdx` → `coreClient.*_client` → `offlineDataManager` 落 SQLite
2. **实时推送**:`ths / tdx` → `coreClient.*_client` → `onlineDataManager` → Redis 流
3. **研究读取**:**所有上层** → `coreClient.data_provider` → `database`/`online` 自动路由

---

## 数据流概览

### 离线数据流(每晚批量)

```
Tushare Pro ───→ coreClient/TushareClient ───→ offlineDataManager
     │                                          │
     │                                          ├─→ SQLite (5 DB / 37 表)
     │                                          │   • db_cn_basic.db
     │                                          │   • db_cn_kpl.db
     │                                          │   • db_cn_index.db
     │                                          │   • db_cn_news.db
     │                                          │   • policy_*.db
     │                                          │
     │                                          └─→ 定时调度:scheduler_updateData
     │                                             • 03:00 凌晨清理(生产真值)
     │                                             • 09:00/18:00/20:00/22:00 增量
     │                                             • 23:00 全量
     │
通达信(pytdx) ──→ offlineDataManager/service_intraday.py
     │                                          └─→ minute / ticks 数据(增量)
     │
开盘啦 ────────→ offlineDataManager/service_kpl_limit_performance.py
                                                └─→ 盘中走实时接口(fetch_realtime)
                                                    历史日走历史接口(get_daily)
```

### 实时数据流(全天)

```
同花顺/通达信 ───→ coreClient ───→ onlineDataManager
                                  │
                                  ├─→ Redis 缓存
                                  │   • 行情快照
                                  │   • 5 档盘口
                                  │   • 分钟 K
                                  │
                                  └─→ SQLite (1 DB / 3 表)
                                     • online_data_YYYYMM.db
```

### 研究读取流(策略层)

```
policyStudy/<策略>/scripts/data_gen.py
        │
        ├─→ subprocess 派发到 offlineDataManager/service_*.py
        │       └─→ service 调用 data_provider(可选 db 或 online)
        │
        └─→ 策略自己读 data_provider 看 db 数据
```

**数据访问铁律**(2026-09-18 拍板):
- ✅ 只调 `coreClient.data_provider`
- ❌ 禁止直接调 tushare_client / kpl_client / tdx_client
- ❌ 禁止直接读 sqlite3
- ❌ 禁止直接用 offline_db_client(除非 data_provider 没暴露的工具)

---

## 版本信息(2026-09-18)

- **离线数据规模**:21 GB(主仓)+ 14 MB(其他)
- **数据表**:**30 数据表 + 7 ctrl 断点表**(2026-09-14 v3 单表+ctrl 表重构)
- **统一接口**:`coreClient.data_provider` **30 个 get_xxx**(覆盖 db 全部 30 个真数据表)
- **Service 进程**:27 个(`offlineDataManager/scripts/service/`)
- **文档**:4 顶层 + 37 代码级 + 11 子模块级
- **测试套件**:**199 个断言,100% 通过**(`coreClient/test/test_data_provider_all.py`)
- **代码仓库**:`git@github.com:LiYiqiang0827/TradingAgent.git`
- **Wiki 仓库**:`git@github.com:LiYiqiang0827/wiki-TradingAgent.git`

---

## 上手顺序

1. **看本文件** — 了解项目全貌
2. **按需看子模块文档**:
   - 想用数据接口? → `coreClient/data_provider.md`(30 接口详细)
   - 想下载新数据? → `offlineDataManager/02_新增数据接入指南.md`
   - 想抓实时? → `onlineDataManager/04_QuickStart.md`
   - 想做策略? → `policyStudy/ARCHITECTURE.md`(10 章架构)
3. **看代码** — 仓库里每个子模块有自己的 README

---

## 变更历史

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-15 | v1.0 | 初版,介绍 4 个子模块的职责和文档入口 |
| 2026-09-18 | v2.0 | **重写**:加 `data_provider` 章节;更新数据规模(5 DB / 37 表 / 30 数据接口);加模块协作关系图;加数据访问铁律;更新版本信息(2026-09-18) |