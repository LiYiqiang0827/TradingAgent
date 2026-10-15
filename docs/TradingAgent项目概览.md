# TradingAgent 项目概览

TradingAgent 维护 MrWu 与 MrLi 的研究、复盘和研究产物。数据代码与数据库是同级的独立目录，所有策略研究只通过统一数据接口读取。

```text
/Users/nickzhang/
├── TradingAgent/
│   ├── MrWu/       # market_review 与 MrWu 研究材料
│   ├── MrLi/       # policyStudy、plot、outputs
│   ├── docs/       # 导航与迁移记录
│   └── integrations/
├── TradingDataManager/   # coreClient.data_provider、采集、落库、服务和调度
└── TradingDatabase/
    ├── offline/
    └── online/
```

## 责任边界

- `TradingDataManager` 提供 `coreClient.data_provider`，管理数据源客户端、采集代码、schema、服务和调度。
- `TradingDatabase/offline` 保存离线行情、新闻、题材、DuckDB 与 Parquet 数据；`online` 保存在线持久化数据。
- `TradingAgent/MrWu` 保存 MrWu 复盘工具与研究；`TradingAgent/MrLi` 保存题材、K线、多周期策略研究、图表及结果。
- `TradingAgent/docs` 保存项目导航和迁移验收；旧顶层目录是兼容链接，不产生第二份实现。

历史回放应显式使用 `source="database_only"`，并保存数据截止时点、样本分母和输入版本。查看统一接口请读 `../TradingDataManager/coreClient/docs/data_provider.md`；研究入口见 `MrWu/policyStudy/policy/market_review/` 与 `MrLi/policyStudy/`。
