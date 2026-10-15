# MrLi 策略研究

这里维护题材、K 线结构、多周期路径研究及相应图表和结果：

- `policyStudy/`：题材研究主体、K 线研究 skill 和研究文档。
- `plot/`：研究图表入口与共享绘图 helper 的唯一实现。
- `outputs/`：与 MrLi 研究相关的历史运行结果。

研究归属按用户提供的上下文确定：Codex 参与的题材、图表、MA 回调和多周期研究归 MrLi。该归属是研究责任划分，不据此推定 Git 作者。`policyStudy/policy/market_review/` 的旧路径通过兼容链接转到 `MrWu/`；MrWu 框架调用的统一 data provider 仍属于数据平台。

策略数据读取应使用 `coreClient.data_provider`。研究根是 `TradingAgent/`；它与 `TRADING_DATA_MANAGER_ROOT` 解析出的数据管理器根不同。严格历史回放应显式使用 `source="database_only"`。
