# AI Agent Start — TradingAgent 项目速读

本仓库负责 MrWu/MrLi 的 A 股研究。数据采集与存储位于同级 `TradingDataManager/` 和 `TradingDatabase/`，不把研究目录当作数据仓库。

## 目录结构

```text
TradingAgent/
├── MrWu/                 # 复盘框架与 MrWu 研究材料
├── MrLi/                 # 题材、K线、多周期研究及研究产物
├── docs/                 # 项目导航、迁移计划与验收记录
└── integrations/         # 项目级集成

../TradingDataManager/    # coreClient、data_provider、数据维护与服务
../TradingDatabase/
├── offline/              # 离线数据库、DuckDB、Parquet
└── online/               # 在线持久化数据库
```

`policyStudy/`、`plot/`、`outputs/` 是指向 `MrLi/` canonical 内容的旧路径链接；`MrLi/policyStudy/policy/market_review` 也是指向 MrWu 工具的兼容链接。只编辑 canonical 实体。

## 数据接口

研究代码经 `coreClient.data_provider` 读取行情、题材和新闻。历史回放使用 `source="database_only"`；这个模式不会在本地缺数时切换到线上。

```python
from coreClient.data_provider import get_day, get_kpl_limit_performance, get_news

df = get_day(
    ts_code="000006.SZ",
    start_date="2026-09-01",
    end_date="2026-09-18",
    source="database_only",
)
```

`coreClient`、`offlineDataManager`、`onlineDataManager` 的历史导入名由 TradingDataManager 的兼容包提供。接口说明：[`../TradingDataManager/coreClient/docs/data_provider.md`](../TradingDataManager/coreClient/docs/data_provider.md)。数据库默认位置在 `../TradingDatabase/offline/` 与 `../TradingDatabase/online/`，具体值由数据管理器配置给出。

## 研究入口

| 工作 | canonical 入口 |
|---|---|
| MrWu 日复盘 | `MrWu/policyStudy/policy/market_review/` |
| MrWu 方法与交付包 | `MrWu/docs/research/` |
| MrLi 题材/龙头研究 | `MrLi/policyStudy/policy/题材涨停研究/` |
| MrLi K线结构分析 | `MrLi/policyStudy/skills/a-share-kline-structure-analysis/` |
| MrLi 题材历史研究 | `MrLi/policyStudy/skills/a-share-theme-research/` |
| MrLi 图表与历史产物 | `MrLi/plot/`、`MrLi/outputs/` |

研究报告须说明数据截止时点、样本分母、输入来源及无法验证项。测试通过或产物存在不自动代表预测能力或交付验收。
