# TradingAgent 画图脚本

本目录存放可复用的行情与研究画图脚本。所有行情数据统一通过
`coreClient.data_provider` 获取。

## 15分钟均线粘合再启动回放图

`plot_fifteen_min_ma_coil_review.py` 读取已冻结的研究结果，绘制MA5/10/20/30/60/120/250、
实际成交量、粘合K线、已完成的信号K线和一分钟入场价格代理；同时输出全期图、局部图、
收益分布和可浏览的`review.html`。涨停成交假设单列，不把价格代理当成真实委托成交。

```bash
MPLBACKEND=Agg PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  plot/plot_fifteen_min_ma_coil_review.py \
  --output outputs/fifteen_min_ma_coil_20260901_20260915_v2
```

策略定义、完整分母、ST与次新过滤、时点校验和停牌复牌的实现限制见
`policyStudy/docs/fifteen_min_ma_coil_replay_20260901_20260915.md`。
该图册属于固定参数的开发期回放，尚未验证稳定盈利。

`plot_fifteen_min_ma_coil_random_review.py` 从上述已冻结的合资格信号中等概率、不放回
随机取样，不使用收益或指定牛股名单选择图片。默认20次、种子20260930。每张JPG包含
信号前30日/后7日的背景和前80根/后32根的局部；展示截止仍为原研究的9月15日。
两层都画实际成交量、七条均线、粘合、确认和一分钟入场代理，信号后行情用灰底分开。
前8根量比与前5日同时段量比并列展示，后者仅供诊断，不改变原筛选。

```bash
MPLBACKEND=Agg PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  plot/plot_fifteen_min_ma_coil_random_review.py --seed 20260930 --count 20
```

默认输出在原回放目录的`random_20_seed20260930/`，包括20张原图、网页图册、缩略总览、
ZIP、随机名单和抽样/图像验收记录。每个抽中信号都会截去未来重新核对，原始冻结信号不改写。

用户当前要求是**每例两张独立图片：15分钟＋日线**，使用
`build_ma_coil_paired_review.py`。它保留已经抽好的20次随机身份，输出40张图，
并对7只用户目标票输出14张图及粘合度表。15分钟展示前30日/后7日，日线展示
前一年/后一月，都截到原研究9月15日。完整观察日日K在盘中尚不可知，因此日线
从观察日整根开始标为灰底事后区；量柱均用真实成交量。

```bash
MPLBACKEND=Agg PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  plot/build_ma_coil_paired_review.py --case-mode both
```

目标票表以已知首板日最大实体阳线前1–2根的粘合测量为参照，属于事后模板研究，
不替代原始信号。原MA30/MA60两组之外也列出含MA60、MA120、MA250的整体价差。
目标输出为`target_7_paired_views/`；随机输出为`random_20_seed20260930/paired_views/`。
两者都有网页、JPG、ZIP和逐图验收记录。

总览按用户最新要求只生成**一张图**：20个股票格子，每格上方为该股日线、
下方为该股15分钟图，不能拆成两张不同周期的总览。已有原图时只需重新拼图：

```bash
PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  plot/build_ma_coil_paired_review.py --case-mode random --overviews-only
```

输出`random_20_seed20260930/paired_views/20例_日线上_15分钟下_总览.jpg`，
并保留40张独立原图便于放大查看；`OVERVIEW_ACCEPTANCE.json`核对每格的两个周期及来源哈希。

当前标准改为两组同时约束：第一组为收盘价+MA5/10/20/30，宽度≤1.0%；
第二组为第一组再加MA60，宽度≤2.0%。同一根已收完15分钟K线两组同时通过。
参数文件为`policyStudy/policy/题材涨停研究/config/ma_coil_dual_group_1pct_2pct.json`。
图中分别显示两组宽度；MA120/250继续负责长期趋势。
此前`ma_coil_strict1pct_candidate.json`仅是旧OR分支的候选，不是当前标准。
旧410次回放及旧图册保留原口径；历史JSON由兼容读取函数恢复原分支定义。
新规则完整说明与重跑命令见[双组粘合约束](../policyStudy/docs/fifteen_min_ma_coil_dual_groups.md)。

## 题材情绪周期

`plot_market_theme_cycle.py` 一次生成两张全市场图：

- 题材情绪周期：0—100情绪分、前三题材绝对热度、涨停/炸板、封板率、
  最高板、主线集中度和五类市场结构；
- 一级题材轮动：5日滚动宽度排名和每日涨停宽度热力图。

```bash
.venv/bin/python plot/plot_market_theme_cycle.py \
  --start-date 20260105 --end-date 20260924
```

`plot_single_theme_cycle.py` 一次生成两张单题材图：

- 生命周期总览：热度、历史百分位、宽度、封板率、梯队、市场排名、涨停份额、
  生命周期色带和已入库催化；
- 核心股演化：逐日涨停、炸板、板高度及龙一至龙三。

```bash
.venv/bin/python plot/plot_single_theme_cycle.py 商业航天 \
  --start-date 20260401 --end-date 20260731
```

两类脚本默认保存到 `plot/tmp_savepic/theme_cycle`。增加 `--as-of YYYYMMDD`
时只展示截止日可见行情，并过滤当时尚未生成的模型分析和事后周期边界。

## 日 K 线

`plot_daily_kline.py` 从 `offlineDataManager` 的本地数据库读取日线，绘制：

- 日 K（A 股红涨绿跌）
- 实际成交量
- MA5、MA10、MA20、MA30、MA60、MA120、MA250
- 用户选择的日期段或指定交易日

价格默认使用前复权，成交量始终使用未复权的实际成交量。脚本只使用
`source="database_only"`，离线缺数时不会自动访问网络。

项目的 `.venv` 已包含所需库。新环境可执行：

```bash
python -m pip install -r plot/requirements.txt
```

### 日期段模式

```bash
.venv/bin/python plot/plot_daily_kline.py 600519 \
  --start-date 2026-03-05 \
  --end-date 2026-06-05
```

默认实际展示范围为 `2025-03-05` 至 `2026-07-05`，并用浅黄色标出
`2026-03-05` 至 `2026-06-05`。

延伸到离线库中的最新交易日：

```bash
.venv/bin/python plot/plot_daily_kline.py 600519 \
  --start-date 2026-03-05 \
  --end-date 2026-06-05 \
  --to-latest
```

默认只显示图形，不保存图片。需要保存时增加 `--save-pic`：

```bash
.venv/bin/python plot/plot_daily_kline.py 600519 \
  --start-date 2026-03-05 \
  --end-date 2026-06-05 \
  --save-pic
```

图片默认保存为：

```text
plot/tmp_savepic/kline_day_600519.SH_20260305_20260605.jpg
```

### 单日模式

```bash
.venv/bin/python plot/plot_daily_kline.py 000001.SZ \
  --trade-date 2026-06-05 \
  --save-pic
```

`--tradedate` 也可以作为 `--trade-date` 的别名。默认展示范围为指定日期前
12 个月至后 1 个月，并突出标记指定交易日。保存文件名中的开始和结束日期
相同，例如 `kline_day_000001.SZ_20260605_20260605.jpg`。

### 多交易日模式

同一只股票可以指定多个交易日：

```bash
.venv/bin/python plot/plot_daily_kline.py 000001.SZ \
  --trade-dates 2026-03-05 2026-04-07 2026-06-05 \
  --save-pic
```

也可以使用逗号分隔：

```bash
.venv/bin/python plot/plot_daily_kline.py 000001.SZ \
  --trade-dates 20260305,20260407,20260605 \
  --save-pic
```

脚本会自动排序并去除重复日期。展示范围从最小交易日向前12个月，到最大
交易日向后1个月，每个指定交易日都会分别用黄色背景和橙色虚线标记。上述
示例保存为：

```text
plot/tmp_savepic/kline_day_000001.SZ_20260305_20260605_multidays.jpg
```

`--lookback-months`、`--lookahead-months` 和 `--to-latest` 对该模式同样有效。

### 买入与卖出标记

任一日期模式都可同时传入 `--buy-date` 和 `--sell-date`：

```bash
.venv/bin/python plot/plot_daily_kline.py 603155.SH \
  --trade-dates 20260610 20260611 20260622 20260623 20260706 \
  --buy-date 20260623 --sell-date 20260706 \
  --save-pic
```

买入日使用绿色上三角和虚线，卖出日使用红色下三角和虚线，三角标在对应日 K 的
开盘价位置；两日之间用浅蓝色标出持仓区间。开盘价只用于定位，
**不表示实际成交价格**。买卖日期必须成对、
有对应日 K，且买入日不晚于卖出日；若超出默认展示范围，窗口会自动扩展。
保存文件名追加 `_buy20260623_sell20260706`，不会与未标记交易的图互相覆盖。

自定义保存目录：

```bash
.venv/bin/python plot/plot_daily_kline.py 000001.SZ \
  --trade-date 2026-06-05 \
  --save-pic \
  --save-dir ~/Pictures/a_share
```

### 可调参数

```text
--lookback-months 12   选择起点前展示多少个月
--lookahead-months 1   选择终点后展示多少个月
--ma-warmup-days 400   在图外额外读取多少天用于计算 MA250
--to-latest            展示到今天（实际到离线库最后一个有数据的交易日）
--no-qfq               使用不复权价格
--save-pic             保存 JPG 图片，默认关闭（也支持 --save、--savepic）
--save-dir DIR         保存目录，默认 plot/tmp_savepic
--dpi 160              输出清晰度
--buy-date YYYYMMDD    买入日期，须与 --sell-date 同时提供
--sell-date YYYYMMDD   卖出日期，须与 --buy-date 同时提供
```

## 批量日 K 线

`plot_daily_kline_batch.py` 复用单图脚本，逐项生成 JPG。JSON/字典列表模式默认
保存到 `plot/tmp_savepic`；watchlist 模式默认保存到
`policyStudy/policy/题材涨停研究/savepic`。两种模式都可用 `--save-dir` 指定其他
目录。同名文件已经存在时默认跳过，增加 `--overwrite` 才会覆盖。

### 读取 policyStudy watchlist

传入任意 watchlist CSV：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py \
  --watchlist policyStudy/policy/题材涨停研究/watchlist/watchlist_tczt_20260101_20260831.csv
```

当前 policyStudy watchlist 包含 `trade_date` 和 `ts_code`，每一行按单日模式
读取。watchlist 模式默认启用“合并相同 ts_code”：同一股票的所有 `trade_date`
会合并成一张多交易日标记图。不写 CSV 路径时，`--watchlist` 使用当前项目的
默认 watchlist：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py --watchlist --limit 10
```

`--limit` 适合先用少量记录试跑。

关闭合并、恢复每行单独绘图：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py \
  --watchlist policyStudy/policy/题材涨停研究/watchlist/watchlist_tczt_20260901_20260915.csv \
  --no-merge-same-tscode
```

合并后，只有一个涨停日的股票仍按单日模式命名；有多个涨停日的股票按
`kline_day_股票代码_最小日期_最大日期_multidays.jpg` 命名。
如果某行包含买卖日期，该行代表独立的一笔交易，不会按股票代码与其他行合并。

### 混合字典列表

输入格式可以同时包含日期段模式和单日模式：

```json
[
  {
    "ts_code": "600519.SH",
    "start_date": "2026-03-05",
    "end_date": "2026-06-05"
  },
  {
    "ts_code": "000001.SZ",
    "trade_date": "2026-06-05",
    "buy_date": "2026-06-08",
    "sell_date": "2026-06-15"
  }
]
```

批量输入也接受 `buydate`/`selldate` 和 `entry_date`/`exit_date` 别名。

仓库提供了 [batch_items.example.json](./batch_items.example.json)，可以直接运行：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py \
  --items-file plot/batch_items.example.json
```

也可以直接传 JSON 字符串：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py \
  --items-json '[{"ts_code":"600519.SH","start_date":"2026-03-05","end_date":"2026-06-05"},{"ts_code":"000001.SZ","trade_date":"2026-06-05"}]'
```

指定保存目录：

```bash
.venv/bin/python plot/plot_daily_kline_batch.py \
  --items-file plot/batch_items.example.json \
  --save-dir ~/Pictures/a_share
```

Python 代码也可以直接传入字典列表：

```python
from pathlib import Path
from plot.plot_daily_kline_batch import plot_batch

summary = plot_batch(
    [
        {
            "ts_code": "600519.SH",
            "start_date": "2026-03-05",
            "end_date": "2026-06-05",
        },
        {"ts_code": "000001.SZ", "trade_date": "2026-06-05"},
    ],
    save_dir=Path("plot/tmp_savepic"),
)
print(summary)
```

CSV 和 JSON 同时支持以下字段别名：`ts_code/tscode/code/symbol`、
`trade_date/tradedate`、`start_date/startdate`、`end_date/enddate`。

## 15 分钟 K 线

`plot_fifteen_min_kline.py` 通过统一接口 `coreClient.data_provider.get_fifteenMin()`
读取本地 DuckDB，绘制 A 股红涨绿跌的 15 分钟 K 线、实际成交量和
MA5/10/20/30/60/120/250。横轴会压缩非交易时间，并标出交易日边界和下午
第一根 K 线（13:15）；库内每个完整交易日为 09:45 至 15:00 的 16 根 K 线。

价格默认前复权，参考因子截至展示终点，不读取更晚的因子。成交量不复权并以万股
显示。指定日模式默认向前展示 30 个自然日、向后展示 7 个自然日；均线还会在
图外额外读取 250 根 K 线预热。15分钟 MA 的周期单位为K线根数，不是日数。

### 单日、日期段和多交易日

```bash
# 单日
.venv/bin/python plot/plot_fifteen_min_kline.py 000001.SZ \
  --trade-date 20260924 --save-pic

# 日期段
.venv/bin/python plot/plot_fifteen_min_kline.py 000001.SZ \
  --start-date 20260901 --end-date 20260924 --save-pic

# 同一股票标记多个交易日
.venv/bin/python plot/plot_fifteen_min_kline.py 000001.SZ \
  --trade-dates 20260923 20260924 --save-pic
```

默认保存目录仍是 `plot/tmp_savepic`。文件名分别为：

```text
kline_15min_000001.SZ_20260924_20260924.jpg
kline_15min_000001.SZ_20260901_20260924.jpg
kline_15min_000001.SZ_20260923_20260924_multidays.jpg
```

常用可调参数：

```text
--lookback-days 30     选择起点前展示多少自然日
--lookback-months 1    按自然月前扩展，覆盖 lookback-days
--lookahead-days 7     选择终点后展示多少自然日
--ma-warmup-bars 250   图外额外读取多少根 K 线用于均线预热
--to-latest            展示到离线库最新交易日
--no-qfq               使用不复权价格
--save-pic             保存 JPG，默认只显示
--save-dir DIR         自定义保存目录
--dpi 160              输出清晰度
--buy-date YYYYMMDD    模拟买入日期，须与 sell-date 成对
--sell-date YYYYMMDD   模拟卖出日期，标记对应日首根K线开盘价
```

例如绘制买入前一个自然月到卖出后一周：

```bash
.venv/bin/python plot/plot_fifteen_min_kline.py 603125.SH \
  --start-date 20260616 --end-date 20260701 \
  --buy-date 20260616 --sell-date 20260701 \
  --lookback-months 1 --lookahead-days 7 --save-pic
```

绿色上三角和红色下三角分别标记两日的开盘价，浅蓝色标出模拟持有区间。
时间戳09:45的首根15分钟K线汇总09:30至09:45，**只使用该根开盘价作为09:30
开盘代理**，不使用09:45收盘价。任一买卖日期缺少首根09:45 K线时会报错。
标记只作回放展示，不保证实际可成交。文件名追加 `_buyYYYYMMDD_sellYYYYMMDD`。

## 批量 15 分钟 K 线

`plot_fifteen_min_kline_batch.py` 支持与批量日线脚本相同的 watchlist CSV、JSON
文件、JSON 字符串和 Python 字典列表，并允许日期段、单日和多交易日项目混合。
watchlist 模式默认合并相同 `ts_code` 的所有 `trade_date`，输出一张多日标记图。

```bash
# watchlist；默认输出到题材涨停研究/savepic/15min
.venv/bin/python plot/plot_fifteen_min_kline_batch.py \
  --watchlist policyStudy/policy/题材涨停研究/watchlist/watchlist_tczt_20260901_20260915.csv

# 混合 JSON 输入
.venv/bin/python plot/plot_fifteen_min_kline_batch.py \
  --items-json '[{"ts_code":"000001.SZ","trade_date":"20260924"},{"ts_code":"600000.SH","trade_dates":["20260923","20260924"]}]' \
  --save-dir plot/tmp_savepic/15min_batch
```

已有同名文件默认跳过；使用 `--overwrite` 覆盖。还可用 `--limit` 小批试跑、
`--no-merge-same-tscode` 关闭 watchlist 合并、`--fail-fast` 在首个错误时停止。

## MA30均线粘合案例图册

`build_ma30_compact_review.py` 在此前5–8月MA30匹配清单上增加 MA5/10/20 最大间距
不超过7%的条件。特征截至信号日收盘，先筛每日匹配再按10交易日间隔去重，保留原有
候选作对照。输出全部候选CSV、通过条件的CSV、筛选日线图、日线买卖回放图、15分钟
回放图及分钟数据核对文件，保存在 `outputs/rising_ma_reactivation/ma30_compact7_review`。

```bash
.venv/bin/python plot/build_ma30_compact_review.py
```

`build_ma30_review_presentation.mjs` 使用桌面内置的 `@oai/artifact-tool` 将完整候选表
做成可编辑表格，并为每个案例排三页图。运行时需将脚本复制到独立构建目录、关联内置
Node依赖，并提供 `DECK_DATA`、`REVIEW_BUILD`、`FINAL_PPTX`、`SKILL_DIR`、
`RUNTIME_NODE_MODULES` 和 `RUNTIME_PYTHON`。原有CSV和图不覆盖，新PPT文件名不能已存在。
详见 [均线粘合复核报告](../policyStudy/docs/ma30_compact7_review_202605_202608.md)。

## 四板案例双周期图册

`build_four_board_chart_review.py` 读取已冻结的四板清单
`outputs/four_board_paths_2026/four_board_paths.csv`，按四板日期排序，每次机会生成两张图：

- 日线：四板日向前12个自然月、向后1个自然月，标记四板当天；
- 15分钟：四板日向前1个自然月、向后14个自然日，黄色区间覆盖当日16根K线，
  即09:30开盘至15:00收盘。首根K线使用09:45的结束时间标签；
- 两图都有实际成交量和MA5/10/20/30/60/120/250。15分钟MA周期按K线根数计算。

```bash
MPLBACKEND=Agg .venv/bin/python plot/build_four_board_chart_review.py
```

默认保存到 `outputs/four_board_chart_review_2026/`，包含原始JPG、图表索引CSV和
`deck_data.json`。图表清单记录请求窗口、实际窗口、四板日根数、缺日检查以及数据和
图片哈希。近期案例保留，只画已经存在的行情，并注明后续窗口尚未满。

`build_four_board_review_presentation.mjs` 将每个案例的两张图分置连续两页，使用4:3
页面展示完整图像，并生成可点击的目录。运行方式与上面的MA30图册相同，提供
`DECK_DATA`、`REVIEW_BUILD`、`FINAL_PPTX`、`SKILL_DIR`、`RUNTIME_NODE_MODULES`、
`RUNTIME_PYTHON`。最终PPT应放在独立的输出目录，不能和私有验证记录放在同一目录。
图册仅用于走势回看，不根据后续涨跌筛选案例。

## 多周期静态结构与未来情景图

`plot_kline_scenarios.py` 接收严格截止到分析日的数据包和冻结分析JSON，一次生成：

- 日线静态K线、实际成交量、MA5/10/20/30/60/120/250和支撑压力；
- 日、周、月联合静态结构图；
- P1至P5动态路径总览；
- 每条路径的假设K线和重算MA5/10/20/60/120。

未来区使用蓝色背景、斜线空心K线和“情景假设区”水印，与真实行情分开。假设K线只表达路径形状；未来成交量没有假设，不参与绘图。

```bash
.venv/bin/python plot/plot_kline_scenarios.py \
  --packet policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/packets/603989_SH_20260618.json \
  --analysis policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/analysis_specs/603989_SH_20260618.json \
  --output-dir plot/tmp_savepic/603989_SH_20260618 \
  --history-bars 80
```

批量验证时可用 `--charts summary` 只生成日线静态、日周月联合和路径总览三张图；
用 `--charts top` 再增加最高权重路径单图。默认 `--charts all` 仍生成P1至P5全部单图。

分析JSON的字段、五路径定义、概率类型和均线抵扣公式见
`policyStudy/skills/a-share-kline-structure-analysis/references/dynamic-projection.md`。

冻结预测评分完成后，可把五条预测路径和评分时才打开的真实未来画在同一张图上：

```bash
.venv/bin/python plot/plot_kline_validation.py \
  --score policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/SCORE.json \
  --analysis-dir policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/analysis_specs \
  --packet-dir policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/packets \
  --output-dir policyStudy/policy/题材涨停研究/research/kline_skill_validation_202606_random3/validation_charts
```

逐根日K更新后，可绘制价格相对U1/S1/D1的位置、第一触发路径证据权重和当前演化路径：

```bash
.venv/bin/python plot/plot_dynamic_path_replay.py \
  --dynamic policyStudy/policy/题材涨停研究/research/kline_skill_dynamic_blind_202607_random20_v1/dynamic_updates/000700_SZ_20260702.json \
  --output plot/tmp_savepic/000700_SZ_20260702_dynamic.jpg
```
