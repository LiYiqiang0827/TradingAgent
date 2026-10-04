# 发布表索引

CSV为UTF-8，日期字段统一ISO日期；`date`是兼容计算核心的YYYYMMDD字段。比例字段一般为0—1，名称含`pct`的档位比例也是0—1；`score`、M1—M5、模块Q值为0—100。空值表示未观测或不适用，不能读成0。

| 文件 | 主键 / 行的含义 | 关键字段 |
|---|---|---|
| sentiment_daily.csv | 2026每市场交易日一行 | score5、grade5、direction、delta1/delta5；正式五模块及质量 |
| formal_daily_2025_2026.csv | 2025与2026每市场日 | 同上；用于复验2025标尺与跨年相位 |
| daily_raw.csv | 含2024末预热的市场日 | k/n、原群体/可观测/停牌/漏行分母；标准化前指标和Q/截断标记 |
| cycle_daily.csv | 2026每市场日 | phase、phase_tag、phase_trigger、phase_consecutive_days、lo5/hi5、breadth_up |
| theme_context_daily.csv | 2026每市场日 | theme_heat_score、structure/code与中文标签、top3_themes、历史窗口、方法与来源质量 |
| theme_rank_daily.csv | 日期×rank | 既有接口的前10题材、热度与核心候选；嵌套证据为JSON字符串 |
| environment_daily.csv | 2026每市场日 | 正式分、独立相位、题材与质量一对一合并；下游接入入口 |
| quality_daily.csv | 2025与2026每市场日 | 逐项身份/上市日/限价/前收/价格/成交额及群体覆盖计数 |
| calibration.json | 固定2025标尺 | priors、pooled_counts、anchors、panic_ddens_p90、版本和校准指纹 |
| clipping_statistics.csv | 年份×标准化指标 | total/valid/missing分母、严格clip_low/high、等于端点、0/100饱和 |
| monthly_summary.csv | 月 | calendar_n、score_valid_n、score_mean、S/A/B/C/D_n/pct、质量日数 |
| monthly_modules.csv | 月×模块 | mean、valid_n |
| grade_transitions.csv / phase_transitions.csv | 起始状态×下一状态 | count、denominator、probability；仅相邻有效市场日 |
| runs.csv | 一段连续状态 | kind、state、start_date/end_date、duration、completed、left/right_censored |
| event_windows.csv | 事件类别×发生日期 | 当日score_t；后1/3/5日score、delta、窗口complete标记及连续事件簇 |
| event_summary.csv | 事件类别 | event_n、consecutive_cluster_n；簇分离不等于统计独立性已证明 |
| same_score_direction.csv | 固定分数区间×方向 | n、score_mean、M1—M5均值；不是严格匹配实验 |
| feedback_lead_lag.csv | 模块变化×偏移 | M4/H_score当日变化与总分在t+offset的单日变化相关、配对N |
| theme_emotion_cross.csv | 档位×题材结构 | n、均分、平均热度 |
| direction_theme_structure.csv | 上/平/下 | 相邻第一题材更替分子/分母、主线结构日数、低档而高热度日数 |
| monthly_rotation.csv | 月 | 第一题材种类与相邻更替次数；月初与前月末相邻边计入当月 |
| divergence_days.csv | 日，按热度减接力分降序 | 两套分数及数值差、结构、前三题材；不同标尺的描述性差异 |
| case_selection.csv / case_windows.csv | 案例 / 案例×相对市场日 | 机械选择规则；前后5日；future_outcome_only明确属于未来展示 |
| human_source.csv / human_review.csv / human_comparison.csv | 原文 / 总控解释 / 合并对照 | 来源定位与对象、短摘录、机器结果、吻合/分歧/可能口径原因 |
| robustness_daily.csv / robustness_summary.csv | a×日期 / a | a=2/5/10，主锚点与先验固定；配对N、分差、档位/相位改变 |
| reference_to_formal_diff.csv | 2025与2026市场日 | B0、逐步修正版、F1分；带符号变化与可加残差 |
| data_change_summary.csv | 年份×修正步骤 | 共同日期N、受影响日数、带符号/绝对增量；非单因素因果效应 |
| external_reconciliation.csv | 10个指定日 | 正式池、宽口径比较池与外部转述；差值和原文冲突说明 |
| input_manifest.json / theme_validation.json | 输入快照 | 库内容SHA256、文件身份、日期范围、来源与可用时点边界 |
| report_numbers.csv | 数值名称×筛选条件 | PDF关键数字的直接来源索引 |

`k_h1...h4/n_h1...h4`是昨日1/2/3/4+板晋级组；`kz_b1...b3/nz_b1...b3`是今日首板/2板/3+触板组炸板计数；`k_mid/n_mid`是昨日2—4板失败并集。original、observed、suspended、missing、unobservable分别保留原群体、有效观测、确证停牌、未证实漏行、有记录但必要价格不可核验；具体字段前缀见原始日表。

`quality_status=PARTIAL`可以与完整五模块分并存：表示有个别股票或来源质量标记，不能解读为整日不可用。正式分缺必要模块才为UNKNOWN。分母是实际观察到的市场日线集合，不能将它误称为已证明包含所有应交易证券。

审计子表包括`calendar.csv`、`data_coverage.csv`、`market_code_coverage.csv`、`namechange_daily_coverage.csv`、`daily_identity_gaps.csv`、`daily_limit_gaps.csv`及`backfill_changes.csv`。它们包含2024末预热和2025校准区间，因此行数不应直接与181日正文统计比较。

全市场逐股events/cohorts属于本机运行成果，体量较大不放Git；从生产入口可重新生成。三日逐股高标及手算工作表随验收证据发布。所有公式和字段单位的解释以[METHOD_AND_DATA.md](METHOD_AND_DATA.md)和固定配置为准。
