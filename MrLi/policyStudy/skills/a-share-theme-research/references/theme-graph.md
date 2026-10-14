# 题材时序知识库

代码、数据库与研究结果使用独立根：`TRADING_DATA_MANAGER_ROOT`（默认 `~/TradingDataManager`）、`TRADING_DATABASE_ROOT`（默认 `~/TradingDatabase`）和 `TRADING_RESEARCH_ROOT`（默认 `~/TradingAgent/MrLi`）。第一版只用开盘啦 `tbl_cn_kpl_list`：

- `lu_desc` 是涨停股当日主题材归因；
- `theme` 是辅助标签；
- 股票范围只含至少一次涨停的股票；
- 炸板记录只用于封板质量和反证，不使一只从未涨停的股票成为知识库股票节点；
- 不读取 `tbl_cn_kpl_concept_cons`，不把普通概念成分混入宽度、核心候选或题材股票列表；
- 暂不使用涨幅大于 7% 的扩展池。

事实库默认位于 `${TRADING_DATABASE_ROOT:-~/TradingDatabase}/offline/db_theme_graph.duckdb`，Obsidian 是可读投影。不要从 Obsidian 反写事实。查询优先使用：

```python
from coreClient.data_provider import (
    get_theme_profile,
    get_theme_members,
    get_theme_daily,
    get_theme_analyses,
    get_stock_theme_history,
)
```

- `get_theme_profile(name=..., as_of=...)`：截至历史时点的题材身份、最近日度状态和周期。
- `get_theme_members(theme_id, as_of=...)`：截至时点按股票聚合的主归因涨停股，不是概念全量成分。
- `get_theme_members(..., historical=True)`：逐次主归因涨停事件。
- `get_theme_daily(...)`：宽度、高度、炸板、热度和程序化阶段。
- `get_theme_analyses(theme_id=... / name=... / episode_id=...)`：已沉淀的周期归因、证据、审计状态、未解决项与模型版本。研究历史题材时先读此接口。
- `get_theme_taxonomy(level1_name=...)`：一级题材及其二级题材映射。
- `get_stock_theme_history(ts_code)`：个股历次主/辅题材归因。

数据库不存在或源 KPL list 已更新时运行：

```bash
python3 "${TRADING_DATA_MANAGER_ROOT:-$HOME/TradingDataManager}/offlineDataManager/scripts/service/service_theme_graph.py" --mode incremental
```

历史回填使用 `--mode backfill --start-date YYYYMMDD --end-date YYYYMMDD`。完全重建使用 `--mode rebuild`。服务按源日期内容哈希幂等更新，默认回看七天以捕获盘后修订，并投影到 `~/Documents/Obsidian Vault/A股题材知识库`。主归因题材按 `dim_theme_taxonomy` 分入一级题材文件夹，原始 `lu_desc` 保留为二级题材；交叉归属只作关联，不复制事实。股票研究页在实际需要时按需创建，题材周期永远保留在所属题材页中，不建立独立周期文档。`_generated` 可重建；人工入口页不覆盖。

题材炒作原因不得从同日新闻直接推断。使用 `build_catalyst_packet.py` 构建有界证据包，Qwen执行逐条筛选，Hermes/MiniMax综合催化和叙事演化，Qwen再审计引用、时序和因果等级。盘后报道只能作为后验解释；无可靠原因时必须保留 `no_reliable_reason`。模型结果写入 `llm_analysis` 且默认待复核，未复核结果不能作为事实或直接进入人工题材结论。自动生成的 Obsidian 题材页会把最新归因写入炒作周期表，并在表下展示完整证据和复核状态。

已有周期必须先复用 `get_theme_analyses()` 的结果。只有新周期、数据修订、用户要求重做，或 `unresolved` / `no_reliable_reason` 指出明确缺口时才补充检索。本地新闻无法形成可靠归因时可联网搜索对应周期及前置窗口；外部证据必须保存 URL、标题、来源、发布时间、抓取时间和对应行情日期，再经过时序与引用审计后写回。搜索结果摘要不能单独作为证据；仍找不到时保留未知。

“某股涨停、板块拉升、概念异动”是行情结果，不能当作炒作原因。纯异动稿只可作为 `market_recap` 或 `late_explanation`；若其中包含政策、公告、产业、价格或技术事件，拆出独立事件并核对最早可见时间，同时优先寻找原始来源。

热度和阶段是候选排序，不是准确率结论。每日状态只用当日及以前事实；episode 的最终结束日属于事后分段，不得作为历史决策输入。详细 schema、评分和维护命令见数据代码根下的 `offlineDataManager/docs/代码详细设计/theme_graph.md`。

本地模型提示词在数据代码根下的 `theme_graph/prompts`。模型结果只写 `llm_analysis` 审计表，不能覆盖 KPL 原始归因。市场高度核心与产业核心必须分开判断。
