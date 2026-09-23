# 数据口径与回放操作

## 项目入口

默认仓库 `/Users/nickzhang/TradingAgent`。先读仓库 `docs/AI_AGENT_START.md`、`coreClient/docs/data_provider.md`、`policyStudy/docs/ARCHITECTURE.md`。底层数据库只由provider访问，研究不直接读SQLite，不改原库。

当前实现的严格离线选项是 `source="database_only"`。普通 `database` 空表时会自动联网，`auto`目前不支持。不要根据旧文档假设它们严格离线。

## 实际数据与用途

| 数据接口 | 必要字段/用途 | 时点和陷阱 |
|---|---|---|
| get_kpl_list | lu_desc主归因、theme多概念、status、lu_time/last_time、limit_order、lu_limit_order、amount、free_float | tags必须显式给列表；None仍只查涨停。本库1—8月13738条涨停、4886条炸板，未返回跌停；炸板lu_desc全部空 |
| get_kpl_limit_performance | board_count、is_break、主题、封单与涨停详情 | 只作交叉核验，勿重复加总；样本存在01:25样式时间，未核实前不能擅自加8小时 |
| get_day(qfq=False) | 原始OHLC、pre_close、pct_chg、vol、amount | 默认qfq=True以最新因子为锚，不用于历史价格阈值。amount为千元，vol为手 |
| get_daily_basic | turnover_rate_f、free_share、volume_ratio、circ_mv | 自由流通市值=free_share(万股)×10000×价格；circ_mv不是自由流通市值。日指标通常盘后才完整 |
| get_stk_limit | up_limit、down_limit | 与当日pre_close识别10%/20%制度，过滤5% ST与无正常涨跌停限制日；按价格判断跌停，不能因KPL没行就记0 |
| get_minute | ts_code、trade_date、datetime、time_idx、price、vol | 实际每分钟一个价格/量点；09:31—11:30、13:01—15:00共240点，没有OHLC和09:25竞价 |
| get_index_daily / get_minute_index | 指数日线/分钟，观察市场风险与同步性 | 不叠加互相重叠的指数成交额；分钟同样是观察点 |
| get_news | datetime、src、title、content、md5、snap_ts | 默认只返回最近5000条；日期参数是整日，精确时点需start_datetime/end_datetime；历史新闻可被回填/修订 |
| get_kpl_concept_cons | name/ts_code概念；con_name/con_code成员；trade_date、desc | 必须取截止时点可见的历史成分版本，不能以今天成员倒推历史；成分完整性未在v1验证 |
| get_adj_factor / get_tradecal | 截至当日的因子、交易日历 | 时间窗口用交易日，节假日不可按自然日平移；若需复权，锚定截至D的因子 |

v1完整市场范围为沪深正常10%/20%涨跌停股票，KPL再按当日名称排除ST/退市标记。使用当日记录，不以最新上市名单移除历史股票。已核验11728个有效涨停股日与原始日线涨停价一致，0个价格错配；5条候选缺合格日线，明列审计。

20%与10%混合高度是v1未解决的局限。正式判断分别列示再比较。盘中用昨日收盘作涨幅参照时，除权日需用当日盘前可见的参考前收；未验证前不将异常跳空视为弱转强。

## 新闻：必须看覆盖

v1固定使用cls到D 18:00，共23314条；160天中有26天为零，不代表没有新闻。多源审计：

| 日期，截至18:00 | 全源条数 | cls条数/说明 |
|---|---:|---|
| 2026-01-05 | 5298 | 221，默认5000会截断全源 |
| 2026-04-30 | 6912 | 230 |
| 2026-07-20 | 1430 | 0，几乎只有sina |
| 2026-08-20 | 4076 | 200 |
| 2026-08-31 | 1266 | 0，只有sina |

多源缺口不能通过“关键词0次”当作利好消失。新版本应保留逐源完整性，跨源去重，分清旧闻、盘后复盘稿与原始事件；支持证据必须先于决策时点。周末/假日及盘前新闻也应纳入新版本，而v1只使用D日到18:00，不声称覆盖全部催化。

```python
from coreClient import data_provider as dp
news = dp.get_news(
    source="database_only",
    start_datetime="2026-01-05 00:00:00",
    end_datetime="2026-01-05 10:30:00",
    limit=None,  # 仅在有界窗口使用；也可limit/offset分页
)
```

新增精确时点/分页参数仅支持本地；进入online路径会拒绝，以免静默忽略时点。需要补数时另行使用provider online或项目服务，保存源、请求、时间和不可补缺口；回放不得静默联网。

## 可见时点

- Tushare说明KPL最终榜单次日早间更新，v1统一在D的下一交易日06:00使用D数据；这只是发布计划假设，缺少历史首发存档，不能证明当时首发值未修订。[KPL官方文档](https://tushare.pro/document/2?doc_id=347)
- daily_basic通常15—17点更新；不得在当天09:45使用当天最终值。[每日指标官方文档](https://tushare.pro/document/2?doc_id=32)
- 新闻datetime为发布时间字段；snap_ts为后来入库时间。严格证据需要事件时间、首次可见时间、抓取时间、修订版本四项。
- 单独拿最终KPL内的bid_pct_chg也无法证明事前有这份名单，不能用最终涨停池还原竞价选股。

## 运行

在项目根目录执行：

```bash
.venv/bin/python policyStudy/policy/题材涨停研究/scripts/run_stage1.py all
.venv/bin/python policyStudy/policy/题材涨停研究/scripts/run_stage1.py snapshot --date 20260630
.venv/bin/python policyStudy/policy/题材涨停研究/scripts/run_stage1.py snapshot --date 20260630 --blind
PYTHONPATH=. .venv/bin/python -m pytest coreClient/test/test_database_only.py policyStudy/policy/题材涨停研究/scripts/test_stage1.py -q
```

输出目录：`policyStudy/policy/题材涨停研究/data/stage1_v1/`，被Git忽略。复用缓存时源保持一致；需要新数据版本应使用`--output`指定新目录，不覆盖旧原始结果。

- `features.json`：160天的市场、题材、个股证据与规则状态。
- `predictions.json`：逐日预测；`labels.json`：评分阶段才可读。
- `daily_ledger.csv`：逐日对错、候选数量与预测原文定位。
- `intraday_predictions.json`：固定前日名单的09:45/10:30/14:00判断。
- `frozen_model.json`：训练截止、标准化系数和样本数。
- `audit.json`、`news_multisource_audit.json`：质量、排除、新闻源覆盖。
- `manifest.json`、`prediction_manifest.json`：缓存、代码、预测和模型SHA256。
- `snapshots/YYYYMMDD/decision_packet.json`：当前日及此前5个交易日，不含未来标签。
- 同目录`blind_decision_packet.json`：独立判断用，去除机械状态/分数与模型预测，附无答案协议、方法及特征字典；历史逐股事实保留。判断器只读这个包即可。

`all` 是机械基线，不调用LLM。盲评LLM时先导出包，向判断器仅提供该包及必要方法参考，禁止给它全期结果文件；写下判断后再运行独立评分。已有v1的指标不得归为LLM成绩。
