# 首波回调后二次启动：2026年1—3月模板开发研究

本轮按用户的近岸蛋白、雷科防务、山东赫达三个模板编程扫描。启动成功研究目标改为持续上涨并具有25%—30%空间；5%不再作为成功口径。**结果没有达到50%的目标，形态被识别不等于买点已具备交易优势。**

[交互图册：全部36例，或只看题材+均线7例](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/index.html) · [冻结信号CSV](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/signals_before_outcomes.csv) · [全部路径结果CSV](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/outcomes.csv)

## 1. 时间顺序和研究范围

- 2026年1—3月是开发区间，三个用户给定案例是已知开发样本。不能称为独立盲测或留出期验证；山东赫达在统计中单列。
- 历史KPL中出现合格首波的1215只股票，扫描68040个股票交易日。强量能版本筛出370个日线候选日、76只股票；不是370次交易。
- 对76只股票逐根回放通达信同源15分钟数据，最终冻结36个信号：1月21、2月6、3月9。每股每个当时可见的首波，只取第一次合格启动。
- 日线背景只到前一交易日收盘；15分钟只能使用已经完成的K线。36个信号通过全数据/截断前缀一致性核验。先冻结规则、输入、代码哈希和信号，再加载未来评价文件。
- 历史KPL涨停名单、历史名称和当时已出现的首波构成候选范围；不按后来涨幅或是否成为牛股筛选。ST、退市名称、ST/次新归因及上市历史不足250个观察交易日者排除。北交所不进入。
- 采用次根15分钟开盘价作为成交代理；时间列仍是该根K线的结束标签，例如14:30表示14:15后下一根的开盘代理，不表示14:30才买。无法验证排队和实际滑点。华银电力次根涨停一价，保守视为未成交，仍保留在36个信号中。

## 2. 当前规则（与旧v1/v2分开保存）

| 模块 | 当前开发规则 |
|---|---|
| 首波 | 2—4板，兼容3天2板；用当日涨停价核实20cm并折合2板；首波高点突破此前63交易日最高价；按首个涨停收盘至高点计涨幅10%—100% |
| 日线趋势 | 高点时短均线高于MA60/120；当前MA5/10/20/30基本处在长均线上方；MA60/120至少一根连续上升20日；MA20/30至少一根当前连续上升15日 |
| 回调与触线 | 高点后4—40日；近5日触到上升MA20/30/60之一，该目标线连续上升至少10日；当前收盘距目标线6%以内 |
| 日线收敛 | MA5/10/20跨度≤5%；当前ATR14占回调阶段最高ATR14比例≤0.92 |
| 阶段量能 | 首波均量≥启动前20日均量4倍；回调均量≥3倍；回调中位量≥基准中位量3倍；回调末5日均量≥基准2.5倍；最低5日滚动均量≥2倍；至少80%回调日量高于基准均量 |
| 回撤 | ≥5%；35%后逐渐降分，35%—40%不硬排除。此版本深度不设硬上限，评分不是成功概率；本次实际信号最大回撤均未到35% |
| 15分钟整理 | 自动搜索已经完成的4/5/8根平台，最高最低宽度≤3.5%；量和平均振幅均≤前一个同长度窗口的80%；后续收盘跌穿平台下沿则该平台过期 |
| 启动 | 12根观察记忆内放量阳线收盘突破平台；阳线实体≥0.35%，实体/全幅≥55%，收盘位置≥70%；相对整理均量≥2倍，相对前5日同钟均量≥1.2倍；不足1%实体时须≥前8根中位实体2倍 |
| 不追远 | 信号收盘高于平台上沿不超过4%，至平台下沿距离≤7%；该7%是信号时距离，次根开盘跳空后实际距离可能增大 |

这里将原先MA20/30连续上升20日的要求放到开发对照，当前版本使用15日，以容纳山东赫达等核心例；没有暗改旧规则或旧实验结果。短整理窗口、12根记忆、ATR和倍量门槛也都是开发设定，不是已证明的市场规律。仅识别局部平台突破；较陡下降线、连续多阳启动、题材精确催化等尚未完整进入本轮自动筛选。

## 3. 成功定义与完整分母

评价窗为实际入场代理所在交易日起10/20个交易日，窗口包含入场日剩余行情。分别统计25%与30%：

1. **有空间**：入场之后的最高价达到目标；不把信号K线的高点算进去。
2. **原路径成立**：达到目标之前，没有15分钟收盘跌穿当时冻结的平台下沿。同根K线同时到目标和失效，保守不算原路径成功。
3. **持续性诊断**：原路径成立且20日内至少3个日收盘高于入场15%。这是附加诊断，暂定而非用户指定的唯一“耗时间”定义。本次25%成立的4例全部满足。

| 窗口 | 有25%空间 | 失效前到25% | 有30%空间 | 失效前到30% |
|---|---:|---:|---:|---:|
| 10交易日 | 4/35 | 3/35 | 2/35 | 2/35 |
| 20交易日 | 7/35 | 4/35 | 5/35 | 3/35 |

35为可评估的成交代理路径；原始信号分母36，另1个保守未成交。20日失效前25%成功率4/35=11.43%；全信号实现该路径比例4/36=11.11%。剔除事先给定的山东赫达后，3/34=8.82%，全发现信号比例3/35=8.57%。全部样本的4/35 Wilson 95%区间约4.54%—25.95%；同题材相关、少样本及开发选择使这个区间也不能当未来概率承诺。

| 月份 | 信号 | 可评估 | 20日有25%空间 | 20日原路径25%成立 |
|---|---:|---:|---:|---:|
| 2026-01 | 21 | 21 | 3 | 2 |
| 2026-02 | 6 | 6 | 1 | 0 |
| 2026-03 | 9 | 8 | 3 | 2 |

## 4. 看收益前固定的题材/均线分组

题材活跃：此前5日同一个KPL主归因出现至少3只涨停股，前一日至少1只。15分钟确认：信号时MA5>10>20>30且都上升，MA60/120均上升。两者只是开发对照，不声称是已验证过滤器。题材归因仅取历史原字面，避免根据赢家事后合并别名；化工、人工智能等宽泛词尤其不能等同具体主线或真实利好。

| 分组 | 信号/可评估 | 有25%空间 | 失效前到25% | 失效前到30% |
|---|---:|---:|---:|---:|
| 全部技术信号 | 36/35 | 7 | 4 | 3 |
| 题材活跃 | 22/21 | 4 | 3 | 2 |
| 均线确认 | 9/9 | 4 | 2 | 2 |
| 题材+均线 | 7/7 | 3 | 2 | 2 |
| 题材+均线，排除赫达 | 6/6 | 2 | 1 | 1 |

题材+均线7个中乾照光电、山东赫达成立，锦鸡股份是先失效后才涨超过25%；剩余4个未满足目标。排除核心案例后只有1/6=16.67%，没有到50%。7个分别1月3、2月1、3月3；并未为了凑少数机会按事后涨幅截取榜单。

## 5. 最值得对照的路径

| 股票 | 信号时间（K线结束） | 10日空间 | 20日空间 | 全窗口最低变化 | 解释 |
|---|---|---:|---:|---:|---|
| 博盈特焊 301468.SZ | 2026-01-06 14:15:00 | 25.66% | 28.86% | -7.22% | 先跌穿原平台，后才达25%；原入场路径判失败 |
| 乾照光电 300102.SZ | 2026-01-08 10:15:00 | 65.96% | 73.91% | -1.04% | 商业航天，题材+均线组；原路径25/30%成立 |
| 山东赫达 002810.SZ | 2026-01-16 14:15:00 | 24.92% | 35.22% | -0.80% | 核心开发例；10日24.92%，20日35.22%；不能用四舍五入充当10日25%成功 |
| 福石控股 300071.SZ | 2026-01-29 09:45:00 | 7.52% | 7.52% | -30.60% | 突然低开；简单平台止损代理亏损约21.21%，窄平台不保证小亏损 |
| 新联电子 002546.SZ | 2026-02-06 11:30:00 | 20.74% | 35.37% | -2.29% | 先失效后涨35%；不能把后来上涨算作原信号成功 |
| 美利云 000815.SZ | 2026-03-04 10:15:00 | 42.56% | 63.44% | -3.68% | 原路径25/30%成立；历史主归因为算力，信号前一日该字面宽度为0，说明题材字面粗筛会漏掉后来的赢家，不能回填未来题材 |
| 锦鸡股份 300798.SZ | 2026-03-19 10:00:00 | 10.02% | 31.83% | -13.95% | 题材+均线组；先失效后涨31.83% |
| 祥明智能 301226.SZ | 2026-03-23 13:15:00 | 27.35% | 27.35% | -3.35% | 原路径25%成立；短均线尚未全部确认，不能事后只保留确认组当唯一买点 |

这轮识别出了用户给定的赫达以及乾照光电、美利云、祥明智能等可观察的二次上涨。但它同时识别出不少只有短促反弹、甚至随后继续下跌的股票。江天化学的更早反弹在3月6日已出现，3月12日才触发本轮平台量价条件；仅要求某个局部平台突破会出现确认过迟。先保留这些失败对照，不根据已看到的结果立刻删掉它们。

## 6. 风险、数据质量和实际限制

- 28/35条路径在20日内收盘跌破冻结平台。失效后的最早可卖次根开盘代理遵循T+1并跳过一价K线；仅扣固定0.4%往返成本作诊断，均值约−6.08%，中位约−6.15%，最差−21.21%。这不是完整卖出系统，没有为成功路径配置止盈，因此没有策略净收益或复利结论。
- 福石控股1月29日9:45信号，次根开盘代理7.45；1月30日收7.23仍高于冻结平台下沿，2月2日首根低开至5.78—6.32、收5.91才确认失效；次根开盘5.90退出代理。说明隔夜跳空会使小平台风险迅速扩大。
- 20日全窗口最低变化中位约−14.81%、最差−30.60%，包含失效后继续观察的行情，不能当执行止损后的账户回撤。
- 交收前不得卖出的规则见[上交所交易规则3.1.4](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)。止损价与真实成交价分开。
- 原始新闻的时间戳证据、热点主线及核心受益关系尚未逐个核实，本轮字段`contemporaneous_catalyst_verified`全部为false；不能说这些股票均有确认的当时利好，也未使用事后LLM归因作信号。
- KPL使用交易日期的历史切片，但源表的历史标签可能有回填/修订；没有当时实际存档快照，因此不能声称复原了原始发布当秒的信息。
- 20日对3月信号的评价按规则向后延伸到4月，必要时到5月；所有这些未来行情都只在信号冻结后加载。

- 日线/15分钟同源交叉核验：3059个完整股票交易日，日收盘、最高、最低全部在1分钱容差内一致；15分钟量/日线量折算后的中位比为1.00。
- 候选日期15分钟覆盖：预期5920根，有效5920根。冻结信号的35条可评估20日路径完整，另1条未成交保留。
- 20项相关单元测试通过；覆盖未来数据污染、平台冻结、信号根高点不计收益、先失效后达标、T+1隔夜亏损、未成交/缺失分母与题材前一日截断。72张图均有成交量、均线、冻结平台和信号标记；决策图与未来图分开。

## 7. 全部36个信号（对照不可删除）

“原路径25%”为20日失效前到达25%；“失效退出”仅列失败诊断代理。不是最终交易表，卖点研究尚未完成。

| 股票 | 时间 | 历史题材 | 回调量/基准 | 日粘合 | 20日空间 | 原路径25% | 失效退出代理净变化 | 图 |
|---|---|---|---:|---:|---:|---|---:|---|
| 四川金顶 600678.SH | 01-05 10:00 | 商业航天 | 5.92倍 | 0.89% | 10.37% | 否 | -6.54% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600678.SH_20260105_1000/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600678.SH_20260105_1000/outcome.jpg) |
| 重药控股 000950.SZ | 01-05 10:15 | 减肥药 | 4.58倍 | 4.72% | 14.96% | 否 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000950.SZ_20260105_1015/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000950.SZ_20260105_1015/outcome.jpg) |
| 博盈特焊 301468.SZ | 01-06 14:15 | 电气设备 | 5.37倍 | 2.92% | 28.86% | 否 | -7.21% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/301468.SZ_20260106_1415/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/301468.SZ_20260106_1415/outcome.jpg) |
| 中瓷电子 003031.SZ | 01-07 10:15 | 商业航天 | 5.41倍 | 3.38% | 15.54% | 否 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/003031.SZ_20260107_1015/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/003031.SZ_20260107_1015/outcome.jpg) |
| 中锐股份 002374.SZ | 01-07 13:15 | 酿酒 | 5.75倍 | 0.37% | 5.66% | 否 | -3.63% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002374.SZ_20260107_1315/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002374.SZ_20260107_1315/outcome.jpg) |
| 乾照光电 300102.SZ | 01-08 10:15 | 商业航天 | 8.28倍 | 3.61% | 73.91% | 是 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300102.SZ_20260108_1015/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300102.SZ_20260108_1015/outcome.jpg) |
| 欢乐家 300997.SZ | 01-08 14:00 | 食品饮料 | 3.30倍 | 2.74% | 14.80% | 否 | -6.62% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300997.SZ_20260108_1400/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300997.SZ_20260108_1400/outcome.jpg) |
| 利群股份 601366.SH | 01-09 11:00 | 零售 | 4.81倍 | 3.49% | 14.68% | 否 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/601366.SH_20260109_1100/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/601366.SH_20260109_1100/outcome.jpg) |
| 威帝股份 603023.SH | 01-12 15:00 | 智能驾驶 | 5.20倍 | 2.53% | 14.91% | 否 | -6.54% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603023.SH_20260112_1500/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603023.SH_20260112_1500/outcome.jpg) |
| 山东赫达 002810.SZ | 01-16 14:15 | 化工 | 3.76倍 | 1.28% | 35.22% | 是 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002810.SZ_20260116_1415/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002810.SZ_20260116_1415/outcome.jpg) |
| 太阳电缆 002300.SZ | 01-19 09:45 | 智能电网 | 7.69倍 | 1.75% | 3.11% | 否 | -7.31% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002300.SZ_20260119_0945/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002300.SZ_20260119_0945/outcome.jpg) |
| 五洲特纸 605007.SH | 01-19 14:00 | 造纸 | 11.33倍 | 1.47% | 22.52% | 否 | -1.89% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/605007.SH_20260119_1400/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/605007.SH_20260119_1400/outcome.jpg) |
| 创元科技 000551.SZ | 01-22 10:45 | 芯片 | 8.43倍 | 2.03% | 2.03% | 否 | -4.33% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000551.SZ_20260122_1045/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000551.SZ_20260122_1045/outcome.jpg) |
| 海昌新材 300885.SZ | 01-23 11:00 | 机器人概念 | 3.62倍 | 2.25% | 1.38% | 否 | -6.59% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300885.SZ_20260123_1100/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300885.SZ_20260123_1100/outcome.jpg) |
| 广西广电 600936.SH | 01-23 11:30 | 并购重组 | 4.31倍 | 3.54% | 2.93% | 否 | -2.88% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600936.SH_20260123_1130/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600936.SH_20260123_1130/outcome.jpg) |
| 华伍股份 300095.SZ | 01-23 15:00 | 机器人概念 | 4.28倍 | 2.15% | 0.00% | 否 | -6.57% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300095.SZ_20260123_1500/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300095.SZ_20260123_1500/outcome.jpg) |
| 药易购 300937.SZ | 01-26 15:00 | 医药 | 4.24倍 | 4.20% | 0.00% | 否 | -3.70% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300937.SZ_20260126_1500/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300937.SZ_20260126_1500/outcome.jpg) |
| 英飞特 300582.SZ | 01-28 13:45 | LED | 3.04倍 | 3.36% | 3.63% | 否 | -4.57% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300582.SZ_20260128_1345/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300582.SZ_20260128_1345/outcome.jpg) |
| 福石控股 300071.SZ | 01-29 09:45 | AIGC | 8.77倍 | 0.39% | 7.52% | 否 | -21.21% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300071.SZ_20260129_0945/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300071.SZ_20260129_0945/outcome.jpg) |
| 宣亚国际 300612.SZ | 01-29 09:45 | 人工智能 | 3.26倍 | 2.32% | 15.48% | 否 | -5.34% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300612.SZ_20260129_0945/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300612.SZ_20260129_0945/outcome.jpg) |
| 岩山科技 002195.SZ | 01-29 10:00 | 脑机接口 | 5.98倍 | 2.22% | 9.51% | 否 | -5.69% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002195.SZ_20260129_1000/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002195.SZ_20260129_1000/outcome.jpg) |
| 新华网 603888.SH | 02-03 09:45 | 人工智能 | 4.02倍 | 0.82% | 5.71% | 否 | -6.42% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603888.SH_20260203_0945/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603888.SH_20260203_0945/outcome.jpg) |
| 人民网 603000.SH | 02-03 13:30 | 人工智能 | 6.99倍 | 0.68% | 12.52% | 否 | -5.35% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603000.SH_20260203_1330/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/603000.SH_20260203_1330/outcome.jpg) |
| 美年健康 002044.SZ | 02-03 13:45 | AI医疗 | 4.90倍 | 3.21% | 1.94% | 否 | -5.54% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002044.SZ_20260203_1345/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002044.SZ_20260203_1345/outcome.jpg) |
| 迪安诊断 300244.SZ | 02-03 13:45 | 人工智能 | 3.78倍 | 3.70% | 2.73% | 否 | -5.90% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300244.SZ_20260203_1345/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300244.SZ_20260203_1345/outcome.jpg) |
| 新联电子 002546.SZ | 02-06 11:30 | 智能电网 | 3.67倍 | 4.57% | 35.37% | 否 | -1.93% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002546.SZ_20260206_1130/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002546.SZ_20260206_1130/outcome.jpg) |
| 红墙股份 002809.SZ | 02-25 10:00 | 化工 | 5.40倍 | 4.04% | 8.21% | 否 | -3.81% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002809.SZ_20260225_1000/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002809.SZ_20260225_1000/outcome.jpg) |
| 广电电气 601616.SH | 03-04 09:45 | 智能电网 | 4.42倍 | 3.27% | 17.73% | 否 | -6.80% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/601616.SH_20260304_0945/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/601616.SH_20260304_0945/outcome.jpg) |
| 美利云 000815.SZ | 03-04 10:15 | 算力 | 5.38倍 | 2.96% | 63.44% | 是 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000815.SZ_20260304_1015/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/000815.SZ_20260304_1015/outcome.jpg) |
| 江天化学 300927.SZ | 03-12 11:30 | 化工 | 3.83倍 | 1.88% | 7.73% | 否 | -6.73% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300927.SZ_20260312_1130/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300927.SZ_20260312_1130/outcome.jpg) |
| 红宝丽 002165.SZ | 03-17 14:30 | 化工 | 6.66倍 | 3.06% | 5.86% | 否 | -5.70% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002165.SZ_20260317_1430/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/002165.SZ_20260317_1430/outcome.jpg) |
| 锦鸡股份 300798.SZ | 03-19 10:00 | 化工 | 3.47倍 | 3.25% | 31.83% | 否 | -6.39% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300798.SZ_20260319_1000/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300798.SZ_20260319_1000/outcome.jpg) |
| 祥明智能 301226.SZ | 03-23 13:15 | 机器人概念 | 3.94倍 | 1.77% | 27.35% | 是 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/301226.SZ_20260323_1315/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/301226.SZ_20260323_1315/outcome.jpg) |
| 华银电力 600744.SH | 03-24 11:30 | 电力 | 3.58倍 | 3.75% | — | 未成交 | — | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600744.SH_20260324_1130/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/600744.SH_20260324_1130/outcome.jpg) |
| 迦南智能 300880.SZ | 03-27 13:15 | 智能电网 | 6.30倍 | 2.32% | 2.00% | 否 | -7.91% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300880.SZ_20260327_1315/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300880.SZ_20260327_1315/outcome.jpg) |
| 通光线缆 300265.SZ | 03-31 15:00 | 商业航天 | 6.06倍 | 4.65% | 12.90% | 否 | -7.24% | [决策](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300265.SZ_20260331_1500/decision.jpg) · [路径](/Users/nickzhang/TradingAgent/outputs/second_wave_templates_202601_202603_v4/charts/300265.SZ_20260331_1500/outcome.jpg) |

## 8. 可复现接口

新增脚本均在`policyStudy/policy/题材涨停研究/scripts`：`study_second_wave_template.py`是无未来特征与路径标签，`replay_second_wave_template.py`分日线/盘中/评价三阶段，`review_second_wave_template_scan.py`读取冻结输入生成图册。

```bash
PYTHONPATH=.:offlineDataManager/scripts:policyStudy/policy/题材涨停研究/scripts \
  .venv/bin/python policyStudy/policy/题材涨停研究/scripts/replay_second_wave_template.py \
  --profile core --output outputs/second_wave_templates_202601_202603_v4 --stage evaluate
```
