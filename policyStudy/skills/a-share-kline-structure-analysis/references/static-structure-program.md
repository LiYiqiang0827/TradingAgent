# 程序化静态结构模块

## 目标

在没有分钟数据时，基于严格截断到 `as_of` 的前复权日线、实际成交量以及聚合周月线，生成可审计的峰谷、水平价格带、缺口、平台、趋势线、经典形态、K线组合、涨停启动区域和日线近似成交密集区。所有证据先由程序产生，模型只能选择、解释和指出冲突，不能重新发明价格或日期。

## 外部实现复用结论

- SciPy `find_peaks` 与 `peak_prominences`：直接用于多尺度峰谷候选和显著度，成熟且已经存在于项目环境。
- scikit-learn `AgglomerativeClustering`：直接用于相近价位聚类。采用 complete linkage，避免DBSCAN链式合并把相距很远的价位连成一个大区间。
- `pytrendline`：MIT许可；其“峰谷—两点候选线—触点—穿越—重复线”思路可借鉴，但其实现会强制加入首尾点、参数基于平均K线振幅，且不包含A股复权、涨停和确认日期。本项目没有复制其代码，而是实现自己的确认峰谷和ATR容差扫描器。
- `trendln`：MIT许可，支持数值微分与Hough方法；依赖和输出语义超出当前需要，暂不作为运行时依赖。
- `day0market/support_resistance`：公开README给出ZigZag或原始峰谷加层次聚类的思路，适合作为算法对照；仓库规模小且许可信息不明确，不复制其代码。
- `chart_patterns`、`stock-pattern` 等项目：可以作为规则和测试样例参考，但当前实现或维护说明暴露了增量峰谷漏检、参数不可审计或许可不清等问题，不直接接入核心流程。
- TA-Lib：后续可作为K线组合候选的可选适配器；当前先实现少量透明规则，避免为形态名称增加C库依赖。
- PatternPy：CC BY-NC-SA，不适合作为本项目可直接复用的核心代码。

## 统一入口

```bash
.venv/bin/python \
  policyStudy/skills/a-share-kline-structure-analysis/scripts/build_static_structure.py \
  --ts-code 000565.SZ \
  --as-of 20260910 \
  --output /tmp/static_000565_20260910.json \
  --plot /tmp/static_000565_20260910.jpg \
  --plot-multitimeframe /tmp/static_000565_20260910_mtf.jpg \
  --plot-timeframes-dir /tmp/static_000565_20260910_timeframes \
  --plot-split-views-dir /tmp/static_000565_20260910_split
```

也可通过 `--packet` 读取已经冻结的 `build_kline_packet.py` 输出。默认尝试从离线库读取真实涨停价；读取失败时退化为日涨幅启发式，并在 `warnings` 中声明。

展示层提供日线细节图、月周日联合总览、月线/周线/日线三张独立细节图，以及推荐使用的三张分层联合图。`01_trend_channels_boxes.jpg` 的三幅面板全部使用日线，分别展示约750/360/180根日K上的外包络、包络趋势线、连续波段、箱体和加速段；`02_support_resistance_gaps.jpg` 展示月周日支撑压力带、缺口、日线近似POC/VAH/VAL及涨停启动锚点；`03_patterns_candlesticks.jpg` 展示月周日经典形态几何、颈线、关键K线组合。价格带标签中的 `D/W/M` 表示日线、周线或月线来源；形态名称后的 `?` 表示仍在形成或等待突破，`✓` 表示已经按规则确认，`↑` / `↓` 表示已经向上或向下破位。

图中绿色半透明带为当前价下方支撑，红色为上方压力，橙色为价格正在其中的争夺带，黄色框为分级箱体，蓝色带为尚未完全回补的缺口。趋势图中的淡红/淡绿细折线分别是逐根最高价/最低价约束下的上、下外包络；粗红/粗绿线分别是从外包络段中选出的当前压力线和支撑线；淡色填充表示上下外包络之间的价格活动范围。深蓝折线按日线确认峰谷首尾相接，实线段表示两端均已确认；最后一个确认拐点到当前收盘使用紫色虚线，表示该段方向仍可被后续K线改写。紫色粗线为尚未经过回踩确认的加速段。三角标记为确认峰谷，`LU` 为涨停启动锚点。周线和月线最后一根K线由截至 `as_of` 的日线聚合而成，图上明确标记为截断中的周期K线，不能当作已经收周或收月。

趋势主骨架按层级保留：大级别用于空间和长期边界，中级别用于当前主波段，小级别用于近期节奏。三个层级统一来自日线，窗口约为750/360/180根。程序先在对数价格中分别计算最高价上外包络和最低价下外包络，使窗口内每根K线都位于两者之间；再把包络折点之间的线段作为趋势线候选。候选按ATR比例容差聚合触点，至少需要两个触点簇；连续三根收盘越过边界才确认突破，向右延伸最长不超过原线段跨度的两倍，避免很久以前的短线无限延长。最后包络端点和仍缺少后续验证的候选标为 `provisional`。同级上、下边界依据斜率和间距变化标为平行通道、收敛或扩张。

确认峰谷仍按long、medium、short依次首尾连接，用来说明价格路径而非替代趋势线；相邻同类峰谷只保留更极端者，使每条确认线段都从高点连向低点或从低点连向高点。日线价格连续至少两根收盘脱离小级别既有轨道、且新斜率显著高于原轨道时，才生成 `provisional_acceleration`；它表示斜率发生变化，不表示新的上升趋势线已经确认。周线和月线继续参与水平价位、平台和形态判断，但不独立拟合斜向趋势线或通道。

## 当前模块

| 模块 | 文件 | 输出 |
|---|---|---|
| 多尺度峰谷 | `static_structure/pivots.py` | 确认峰谷、确认日期、显著度、临时极值 |
| 水平价格带 | `static_structure/zones.py` | 多来源价格带、触碰、周期、证据分 |
| 缺口 | `static_structure/gaps.py` | 上下沿、部分/完全回补状态 |
| 平台箱体 | `static_structure/consolidation.py` | 大/中/小级别区间边缘、触碰、收缩、突破状态 |
| 价格外包络 | `static_structure/envelopes.py` | 逐根最高/最低价外包络、候选趋势线、触点簇、突破生命周期和通道几何 |
| 趋势线与通道 | `static_structure/trendlines.py` | 分级趋势线、稳健平行通道、通道突破和临时加速段 |
| 经典形态 | `static_structure/patterns.py` | 双顶底、头肩、三角/楔形、矩形、旗形 |
| K线组合 | `static_structure/candlesticks.py` | 形状候选，明确要求结构上下文 |
| 涨停启动位 | `static_structure/limit_up.py` | 启动区、平台边缘、回踩和失效 |
| 日线近似价量分布 | `static_structure/price_profile.py` | 近似POC/VAH/VAL/HVN，固定低置信度 |
| 组合与门禁 | `static_structure/engine.py` | `a_share_static_structure.v1` |
| 绘图 | `static_structure/render.py` | K线、均线、价格带、平台、缺口和趋势线 |

## 分钟数据接入点

分钟数据准备完成后，只替换 `price_profile.py` 的数据源并增加盘中涨停质量模块。`horizontal_zones`、绘图和模型输入的数据结构保持兼容。日线近似结果必须一直保留 `source_granularity=daily_approximation` 和 `confidence=low`，不得与真实分钟成交量分布混用。

## 评分边界

当前 `evidence_score` 只用于同一只股票内部排序，尚未通过历史样本校准，不代表反弹概率或上涨概率。后续应冻结结构结果后，分别评价价格带触达后的反应、趋势线失效、形态确认率和误报率。
