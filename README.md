# TradingAgent

A 股策略研究与数据系统。策略代码和研究产物按研究归属放在 `MrWu/`、`MrLi/`；数据代码由同级的 `TradingDataManager/` 提供，数据库统一放在 `TradingDatabase/offline/` 与 `TradingDatabase/online/`。

新接手先读 [`docs/AI_AGENT_START.md`](docs/AI_AGENT_START.md)。研究规范见 [`MrLi/policyStudy/docs/ARCHITECTURE.md`](MrLi/policyStudy/docs/ARCHITECTURE.md)，数据接口文档见 [`../TradingDataManager/coreClient/docs/data_provider.md`](../TradingDataManager/coreClient/docs/data_provider.md)。

## 目录

```text
/Users/nickzhang/
├── TradingAgent/
│   ├── MrWu/                 # MrWu 复盘工具与研究包
│   │   ├── policyStudy/policy/market_review/
│   │   └── docs/research/
│   ├── MrLi/                 # 题材、K线、多周期研究
│   │   ├── policyStudy/
│   │   ├── plot/
│   │   └── outputs/
│   ├── docs/                 # 项目入口、重构计划与迁移记录
│   └── integrations/         # 项目集成
├── TradingDataManager/      # clients、data_provider、下载/落库/调度
└── TradingDatabase/
    ├── offline/              # 离线库与分区数据
    └── online/               # 在线持久化库
```

根目录的 `policyStudy`、`plot`、`outputs` 和 MrWu 旧目录是相对符号链接，只用于兼容旧命令；正式内容只维护在 owner 目录。数据代码搬迁期间，`coreClient`、`offlineDataManager`、`onlineDataManager` 的旧导入名由兼容入口保持。

## 数据访问约定

策略层经 `coreClient.data_provider` 读取数据，不直接访问原始 client 或数据库文件。严格历史回放使用 `source="database_only"`，缺数时返回缺失而不回退线上。

```python
from coreClient.data_provider import get_day, get_kpl_limit_performance

df = get_day(
    ts_code="000006.SZ",
    start_date="2026-09-01",
    end_date="2026-09-18",
    source="database_only",
)
```

数据根由 `TradingDataManager` 的配置解析，数据库根是 `TradingDatabase`；不要把研究根当成数据根，也不要在代码仓库生成空库来掩盖路径错误。读取接口和可用字段见 `TradingDataManager/coreClient/docs/data_provider.md`。

## 研究与验证

- MrWu 复盘工具：`MrWu/policyStudy/policy/market_review/`。
- MrLi 题材研究：`MrLi/policyStudy/policy/题材涨停研究/`。
- MrLi K线结构 skill：`MrLi/policyStudy/skills/a-share-kline-structure-analysis/`。
- MrLi 题材研究 skill：`MrLi/policyStudy/skills/a-share-theme-research/`。
- 图表入口随研究保存在 `MrLi/plot/`；跨研究共用函数只保留一个实现。

历史结果必须保留来源日期、数据截止时点、输入版本和失败样本。进度、图表生成或测试数量本身不代表研究验收。
