# 数据契约

## 数据来源

使用 `coreClient.data_provider` 的统一接口：

- `get_day`：日线，价格可取前复权；
- `get_week`、`get_month`：需要数据库现成周月线时使用；
- `get_basic`：股票简称等静态信息；
- `plot/plot_daily_kline.py` 与 `plot/plot_daily_kline_batch.py`：生成视觉复核图。
- `plot/plot_kline_scenarios.py`：根据冻结数据包和分析JSON生成日线静态图、日周月联合图、动态路径总览和逐路径假设K线/均线图。
- `scripts/score_scenario_paths.py`：冻结预测后才读取未来20个交易日，评分第一触发、路径链、Brier分数和五日形状。
- `plot/plot_kline_validation.py`：把冻结路径与评分时才打开的真实未来画在同一张对照图中。
- `scripts/build_static_structure.py`：从冻结数据包或 `ts_code + as_of` 生成程序化静态结构JSON、日线覆盖图、月周日联合图、三个周期的独立细节图，以及趋势骨架/关键位置/形态K线三张分层图。

历史盲测固定 `source="database"`，不得触发在线补数。价格默认前复权，成交量和成交额使用原始实际值，避免把成交量随复权因子改变。

## 生成严格时点数据包

```bash
.venv/bin/python policyStudy/skills/a-share-kline-structure-analysis/scripts/build_kline_packet.py \
  --project-root /Users/nickzhang/TradingAgent \
  --ts-code 000565.SZ \
  --as-of 20260910 \
  --output /tmp/000565_20260910.json
```

数据包包含：

- 默认约750根前复权日线OHLC和实际成交量，用于日线大、中、小级别趋势细读；
- 默认约1250根截断日线的背景窗口，用于聚合最多约5年的周线和月线结构；
- MA5/10/20/30/60/120/250、斜率和乖离；
- 5/20/60日均量与量比；
- ATR14、当日收盘位置和20/60/120/250日区间位置；
- 仅由截止日前数据确认的摆动点；
- 放量、宽幅、涨跌停候选事件；
- 从同一份截断日线聚合出的周线和月线；未完成的当周/当月只使用截至 `as_of` 已发生的交易日，并以最后一个真实交易日标记，避免未来日期标签和未来日数据。

## 程序化静态结构包

`scripts/build_static_structure.py` 输出 `a_share_static_structure.v1`，包括：

- 日周月多尺度确认高低点及确认日期；
- 多来源水平支撑压力带；
- 缺口和大/中/小级别平台；
- `swing_paths`：只基于日线，将大/中/小级别的交替确认峰谷首尾相接；最后一个确认拐点到当前收盘单独标为临时末段；
- `price_envelopes`：只基于日线，在约750/360/180根K线窗口内用每根K线的最高价、最低价生成上/下外包络折线；最后端点始终为临时点；
- `envelope_trendlines`：从外包络线段中按触点簇、跨度、ATR容差、失效状态和时效性筛选出的支撑/压力趋势线候选；
- `envelope_geometry`：同级别上、下包络趋势线的平行、收敛或扩张关系，以及该判断是否仍为临时状态；
- `trendlines`：只基于日线的确认峰谷形成、用于机器审计的支撑/压力拟合线；
- `trend_channels`：只基于日线，用稳健中线与平行包络形成的大/中/小级别通道、边界和突破状态；
- `acceleration_legs`：连续脱离小级别通道后的临时加速段及其相对原通道的斜率倍数；
- 经典形态状态；
- 涨停启动区域与回踩/失效状态；
- K线组合候选；
- 日线近似POC、VAH、VAL和高成交/高时间密集区。

在分钟数据接入前，成交量按每日价格区间近似分配，必须保留 `source_granularity=daily_approximation` 和 `confidence=low`。该结果用于定位历史密集区，不得称为真实逐笔筹码分布。

## 图像和数值的职责

- 数值负责日期、价格、涨跌幅、成交量、均线和关键位。
- 图像负责识别大尺度形态、走势节奏和候选区间。
- 图像与数值冲突时，以数值为准，并记录冲突原因。
- 最终结论中引用关键数值，而不是只说“图上看起来”。
