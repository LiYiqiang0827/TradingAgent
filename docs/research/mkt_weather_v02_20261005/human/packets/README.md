# Harry 与 Li 独立标注说明

请各自阅读 facts.md，在自己的标签 CSV 中填写；先独立标注，再讨论。

- hit（挨打）：轻 / 中 / 重。
- cont（延续）：差 / 一般 / 好。
- act（活跃）：低 / 中 / 高。
- weather（最接近的天气）：晴 / 多云 / 阴 / 雷阵雨 / 暴雨。
- notes：可选备注；不确定的标签可以留空。

昨日群体按昨日合格涨停身份固定；连板群体包括昨日全部 ≥2 板。今日大跌为收盘相对有效前收跌 5% 及以上或收于跌停，每只只计一次。比例的分母是今日可观测人数。

涨跌幅指数用百分点存储，例如 1.2 表示 +1.2%；上涨占比和群体大跌率的 CSV 原始值为 0–1。晋级 ≥4 板组包括更高板。空比例表示分母为零或无法观测。

nominations_template.csv 用于补充约15个记得清楚的日期，填写 YYYY-MM-DD；允许两人重复提名，程序会去重。

## facts.csv 字段

| 字段 | 含义 | 单位 |
|---|---|---|
| mkt_eligible_n | 合格股票数 | 只 |
| mkt_up_n | 涨停数 | 只 |
| mkt_down_n | 跌停数 | 只 |
| mkt_broken_n | 炸板数 | 只 |
| mkt_max_height | 最高连板 | 板 |
| mkt_turnover_cny | 全市场成交额 | 元 |
| mkt_ma20_cny | 此前20交易日成交均额（不含当日） | 元 |
| mkt_ratio20 | 成交额/此前20日均额 | 倍 |
| mkt_index_sh_pct | 上证指数涨跌幅 | %（百分点） |
| mkt_index_sz_pct | 深证成指涨跌幅 | %（百分点） |
| mkt_index_cyb_pct | 创业板指涨跌幅 | %（百分点） |
| mkt_advance_n | 上涨家数（平盘不计） | 只 |
| mkt_advance_pct | 上涨家数占比 | 0–1 |
| mkt_height_1_n | 当日1板人数 | 只 |
| mkt_height_2_n | 当日2板人数 | 只 |
| mkt_height_3_n | 当日3板人数 | 只 |
| mkt_height_4_n | 当日4板人数 | 只 |
| mkt_height_5_n | 当日5板人数 | 只 |
| mkt_height_6plus_n | 当日6板及以上人数 | 只 |
| mkt_ladder | 有成员的连板档数/6 | 0–1 |
| mkt_all_original_n | 昨日全部涨停群体原始人数 | 只 |
| mkt_all_observed_n | 昨日全部涨停群体今日可观测人数 | 只 |
| mkt_all_paused_n | 昨日全部涨停群体今日停牌人数 | 只 |
| mkt_all_missing_n | 昨日全部涨停群体今日缺失人数 | 只 |
| mkt_all_unobservable_n | 昨日全部涨停群体今日有记录但不可观测人数 | 只 |
| mkt_all_drop_k | 昨日全部涨停群体今日大跌人数 | 只 |
| mkt_all_drop_rate | 昨日全部涨停群体今日大跌比例（分母为可观测人数） | 0–1 |
| mkt_chain_original_n | 昨日≥2板群体原始人数 | 只 |
| mkt_chain_observed_n | 昨日≥2板群体今日可观测人数 | 只 |
| mkt_chain_paused_n | 昨日≥2板群体今日停牌人数 | 只 |
| mkt_chain_missing_n | 昨日≥2板群体今日缺失人数 | 只 |
| mkt_chain_unobservable_n | 昨日≥2板群体今日有记录但不可观测人数 | 只 |
| mkt_chain_drop_k | 昨日≥2板群体今日大跌人数 | 只 |
| mkt_chain_drop_rate | 昨日≥2板群体今日大跌比例（分母为可观测人数） | 0–1 |
| mkt_promo_h1_k | 昨日1板今日晋级数 | 只 |
| mkt_promo_h1_n | 昨日1板可观测晋级分母 | 只 |
| mkt_promo_h2_k | 昨日2板今日晋级数 | 只 |
| mkt_promo_h2_n | 昨日2板可观测晋级分母 | 只 |
| mkt_promo_h3_k | 昨日3板今日晋级数 | 只 |
| mkt_promo_h3_n | 昨日3板可观测晋级分母 | 只 |
| mkt_promo_h4_k | 昨日≥4板今日晋级数 | 只 |
| mkt_promo_h4_n | 昨日≥4板可观测晋级分母 | 只 |
