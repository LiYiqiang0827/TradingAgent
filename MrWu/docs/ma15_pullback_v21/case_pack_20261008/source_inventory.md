# 案例资料定位清单

2026-10-08｜仅资料、元数据与实现定位；由主控验收。未读行情表、未计算信号收益、未打开考卷。

## 1. 原始来源与尚未确认的日期

已先读A2，再读最新规划交接、修订计划与有效设计v2.1。原始老师资料本次经Google Drive连接实读：

- [李老师_均线15分钟策略构想_20261005.md](https://drive.google.com/file/d/1OnCgYB9NFBPm7ubgypKT0bGf5twUiM_A/view)，末段原话：“参考近岸蛋白9月份，雷科防务12月份，精智达6月份4月份，京东方6月份，山东赫达万向德农8月 康龙化成8月。”
- [李老师_确认问题回复_20261005.txt](https://drive.google.com/file/d/1NBmfEgc85uTrJRyy37B5tlLkIqjfhirY/view)，第六节案例表代码、心中买点、卖点栏均空白。雷科防务行的“12月（2025年？）”是待确认的问题文字，不是老师已确认年份。山东赫达行写“月份？”，故原构想紧邻的8月不能无条件认定为赫达的已确认月份。

下表证券名称、代码、板块、上市日于本次只读查询 `D:\ProjectRocketData\legacy_C_Project_Rocket\Trading Agent Database\db_cn_basic.db` 的 `tbl_cn_basic` 核实；未查询该库行情表。

| 股票 | 代码／板块 | 原始月份 | 年份、精确点位的证据状态 | 已有旁证（不能代替老师确认） |
|---|---|---|---|---|
| 近岸蛋白 | 688137.SH／科创板 | 9月 | 未确认 | main旧模板报告明确观察日2026-09-14、此前2026-09-11结构已就位；该报告属于另一描述试验 |
| 雷科防务 | 002413.SZ／主板 | 12月 | 年份未确认；回复中的2025带问号 | 未定位到老师本策略精确日期 |
| 精智达 | 688627.SH／科创板 | 4月和6月 | 两个窗口都保留；年份未确认 | 未定位到老师本策略精确日期 |
| 京东方A | 000725.SZ／主板 | 6月 | 年份未确认 | 原文“京东方”按有效设计映射京东方A，代码已由元数据核实 |
| 山东赫达 | 002810.SZ／主板 | 原构想可能共指8月；回复标“月份？” | 月份与年份均待确认 | 不能把8月候选图标成老师确认窗口 |
| 万向德农 | 600371.SH／主板 | 8月 | 本次原始回复未确认年份/日期 | 本地项目回顾记录老师8月26日买入；2026年8月机制报告也有08-26一行，但不是本策略买点确认 |
| 康龙化成 | 300759.SZ／创业板 | 8月 | 年份未确认 | main旧模板为2026-07-31，不能把7月末替换为本次已确认8月买点；旧报告称该波未有自身涨停，属结构参照 |

上市日元数据：近岸蛋白20220929、雷科防务20100528、精智达20230718、京东方A20010112、山东赫达20160826、万向德农20020916、康龙化成20190128。上市年份元数据并非行情访问授权。

用户最新补充由主控转达：“主要就是今年。你自己寻找方法，在这些股票今年的表现里寻找符合李老师策略的时段。”因此实际候选输出固定为2026-01-05至2026-09-18七股，2025仅作必要指标预热，不生成2025案例结果；原始月份作为旁证，不限制今年按冻结规则查找。历史精确买卖点仍未得到老师确认。科创/创业板仅作形态理解。

## 2. 应在案例上核对的老师原话

逐条回复A1：先看程序能否找到同样的点；关键还是买卖配合，及时识错降低失败亏损，让走出第二波的交易贡献盈利。

- C1：“至少要48根以上（3天）”；不能把任意浅回调默认为满足空头排列。
- C3：“12根内全部完成，顺序不强制。”
- C4：“金叉完成后、回踩买入前，至少一根收盘在MA120上方”。
- C7：“回调立即买”；C8：“不回踩就不买”；C10：“15分钟不看成交量”。
- D1：“盘中买入看之前的日K，当天只看15分钟是否摸到了日线的均线”；D2：“前一天”。
- E1：“期望状态就是连续几天的拉升也就是15分钟连续48根以上的拉升”；与原构想“16根K线范围内还没有走出……直接卖出”需分开呈现早期R16与事后L48，不等待未来48根才买入。
- E2接受买入根起算、T+1不能卖则次日第一根开盘；E3“16根里面设置10%的止损”。
- F1失败例：“这个就是你的事情了，你选出来一定会有反例的”。没有老师另给的失败股清单；反例选择规则由当前包事先冻结，不事后挑漂亮样本。

以上用来核对理解；实际数值、状态、标签仍服从有效设计v2.1及当前明确修订，不用老师早期答复覆盖已定规则。

## 3. 数据入口及可用窗口（仅目录证据）

- 15分钟CSV查询库：`D:\ProjectRocketData\processed\TradingAgent\fifteenMinute\fifteen_min.duckdb`；目录表 `fifteen_min_ingest_catalog`。旁边 `fifteen_min_publish.json` 本次读取：max_date=20260918、4304日期、241552208行，日期覆盖是全库元数据，不构成读取封存年份授权。
- 原始CSV：`D:\ProjectRocketData\legacy_C_Project_Rocket\Trading Agent Database\A Share Miniutes Data\15min\2026`；历史解包层：`D:\ProjectRocketData\processed\TradingAgent\staging\minutes\15min`。存在性/总体日期依据本地 `sources/infrastructure_dictionary_20261006.md` 第3节，未逐只逐月验证完整性。
- 1分钟目录：`D:\ProjectRocketData\processed\TradingAgent\oneMinute\parquet\schema_v2`，目录库同级 `catalog.duckdb`，本次未读取市场行，也没有新增1分钟核账工作。
- TDX增量另在 `D:\ProjectRocketData\processed\TradingAgent\db_fifteenMinute.db`，不能与CSV源混充；日线已到9/30也不代表本源分钟超过9/18。
- 当前候选资料可用上限：2026-09-18；2025年目录层有完整年度。七只股票各局部窗口的缺失、16根与预热完整性仍须由案例读取器逐窗检验，本清单没有声称已质量通过。

## 4. 可复用实现与已存产物

### 现工作树（实际存在）

- `policyStudy/policy/ma15_pullback_v21/src/indicators/hold_constraint.py`：`calculate_hold_constraint`，复用精确守住价边界，不重写。
- `policyStudy/policy/ma15_pullback_v21/src/indicators/warmup_state.py`：已有因果预热、均线快照及质量血缘接口；复用前核实与新输入口径衔接。
- `policyStudy/skills/a-share-kline-structure-analysis/scripts/project_ma_scenarios.py`：均线抵扣的纯价格路径计算可参考；不启动另一研究流程。
- 开盘与F2测试已存在于 `policyStudy/policy/ma15_pullback_v21/tests/`，包括 `test_opening_asof.py`、`test_opening_adapter_integration.py`、`test_name_conflict_v16.py`。名称带历史版本不表示恢复旧调查；只复用对应不变约束。

### main仓库的既有组件（须按文件复用，不能恢复旧策略）

路径根：`C:\Project Rocket\Codex\TradingAgent`。

- `policyStudy/policy/题材涨停研究/scripts/study_rising_ma_reactivation.py`：`prepare_daily`与`plot_template`提供日线MA/实际量和图表基础；EXAMPLES明确300759.SZ/20260731、688137.SH/20260914。其旧阈值与独立日线输入不能直接当本次v2.1规则。
- `policyStudy/policy/题材涨停研究/scripts/study_core_reactivation_minute_execution.py`：`execute_minutes`、`minute_schedule`等旧因果/T+1参考。当前工作树没有此文件，位于main；其本身并非本次策略的验收实现。
- `policyStudy/docs/rising_ma_reactivation_chart_templates_2026.md`：旧三例文字与逐项判定，记录上述近岸、康龙确切观察日；未证明老师这次指定的精确窗口。
- `outputs/rising_ma_reactivation/local_examples_20260930.json`：实际存在的旧三例数值摘要，可核对历史事实，但不能作为新口径结果。`outputs`是到D盘的junction。
- 旧报告提到的 `outputs/rising_ma_reactivation/charts`、`may_aug_2026_final` 图目录在本次直接枚举目标目录时未发现，故不能宣称现成PNG可直接交付。本次也未定位到覆盖七股的既有案例图或v2.1事件缓存。

不确定项已完整保留：年份/精确点位未知、山东赫达月份疑义、逐窗完整性待验、新旧口径导致的差异待算。清单不解锁S0/S1，不调整旧STOP。
