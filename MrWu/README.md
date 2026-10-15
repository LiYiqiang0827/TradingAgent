# MrWu 研究与复盘

这里维护 MrWu 原有的市场复盘框架和 MrWu 来源的研究资料。主要入口：

- `policyStudy/policy/market_review/`：市场复盘工具与其本地合同、说明和脚本。
- `docs/research/`：`mrwu_*` 研究包与 `mkt_weather_*` 研究材料。
- `docs/ma15_pullback_v21/`：MA15 回调研究材料。

归属依据是用户提供的历史项目上下文，以及 `MrWu` 分支独有的包和文档来源；Git 作者信息只有 nickzhang 与 Codex Local Engineering，不能单独证明研究作者或所有权。`ma15_pullback_v21` 当前按用户指定优先归入 MrWu，但其目录来源与研究主体仍需后续核实，故不作为 100% 确认的作者归属。

数据读取继续走统一 `coreClient.data_provider` 公共接口。数据 provider、client、调度和数据库维护由同级 `TradingDataManager/` 与 `TradingDatabase/` 管理，不因复盘框架调用它们而成为 MrWu 研究代码。
