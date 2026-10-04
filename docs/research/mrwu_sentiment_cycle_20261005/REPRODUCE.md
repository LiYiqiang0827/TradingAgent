# 两条复刻路线

以下命令从仓库根目录执行，使用PowerShell和Python 3.12。路径参数换成本机目录即可；不要求Codex、付费Office或Harry云盘。报告生成使用ReportLab与Matplotlib，中文字体随包按OFL 1.1免费分发。

## 路线一：用随附派生表重建PDF

```powershell
git clone --branch MrWu --single-branch https://github.com/LiYiqiang0827/TradingAgent.git
cd TradingAgent
py -3.12 -m venv .venv-sentiment-report
.\.venv-sentiment-report\Scripts\python.exe -m pip install -r docs/research/mrwu_sentiment_cycle_20261005/reproduction/requirements.txt
.\.venv-sentiment-report\Scripts\python.exe docs/research/mrwu_sentiment_cycle_20261005/reproduction/build_deck.py --data docs/research/mrwu_sentiment_cycle_20261005/data --output .local/sentiment-report-rebuild/report.pdf
.\.venv-sentiment-report\Scripts\python.exe docs/research/mrwu_sentiment_cycle_20261005/reproduction/render_pdf.py .local/sentiment-report-rebuild/report.pdf --output .local/sentiment-report-rebuild/render
```

仓库是既有私有仓库，clone需使用已有访问权限。路线一不需要任何行情数据库、Tushare令牌或网络行情补数。`build_deck.py`自动定位附带`RocketSansSC.ttf`；`--font`可以显式指定兼容字体。`fonts/OFL.txt`是原始许可；字体从Noto Sans SC生成，已使用新的家族名。`make_font.py`仅供维护字体子集，常规复刻不运行它。

`reproduction/requirements-lock.txt`另保存实际隔离环境的完整依赖版本，需要尽量匹配此次渲染时可用它替代上述requirements.txt。全部为公开免费库。

仓库的路径限定`.gitattributes`保留本次交付文件的字节与换行，不受Windows `core.autocrlf`改写，以便跨机验证SHA256；未改变其他研究路径的换行策略。

生成24页16:9 PDF，输出旁的`page_sources.json`列每页表格来源。`data/report_numbers.csv`列核心数字；完整日表和统计表是经验数字的直接依据。渲染检查是补充证据，不能代替打开PDF检查。

## 路线二：从本机数据复算

先准备完整、停止写入的SQLite副本目录，至少含`db_cn_basic.db`、`db_cn_kpl.db`及审计需要的`db_cn_index.db`。basic含日线、每日限价、证券基本资料、SSE市场日历、停牌和历史名称区间。不要把只复制了一部分的文件当有效库。表、字段、单位和缺失规则详见[METHOD_AND_DATA.md](METHOD_AND_DATA.md)。

```powershell
py -3.12 -m venv .venv-sentiment-calc
.\.venv-sentiment-calc\Scripts\python.exe -m pip install -r policyStudy/policy/speculation_sentiment_lite/requirements.txt
$sentimentData = 'E:\TradingAgentData\live'
$sentimentRun = 'E:\TradingAgentData\sentiment-reproduction'
.\.venv-sentiment-calc\Scripts\python.exe policyStudy/policy/speculation_sentiment_lite/production.py --data-root $sentimentData --output "$sentimentRun\formal" --end 20260930
$env:TRADING_AGENT_FROZEN = '1'
.\.venv-sentiment-calc\Scripts\python.exe policyStudy/policy/speculation_sentiment_lite/theme_export.py --repo-root . --theme-db 'E:\TradingAgentData\db_theme_graph.duckdb' --output "$sentimentRun\theme" --start 20251224 --end 20260930
.\.venv-sentiment-calc\Scripts\python.exe docs/research/mrwu_sentiment_cycle_20261005/reproduction/analyze.py --formal "$sentimentRun\formal" --theme "$sentimentRun\theme" --output "$sentimentRun\analysis" --human-source docs/research/mrwu_sentiment_cycle_20261005/data/human_source.csv --human-review docs/research/mrwu_sentiment_cycle_20261005/data/human_review.csv
```

`--end`缩短查询上限；前缀检验必须另外传`--fixed-calibration <正式calibration.json>`，保持同一2025标尺。`--baseline-dir`可指向本机B0运行结果以计算旧版到正式版的逐步差异；不提供旧快照时仍可复算F1，但不能声称复验了B0。

题材接口要有2026之前的历史，以形成最多120日窗口。已有题材库可以只读查询；如需用本机KPL数据增量更新，应在隔离派生库副本运行已有`service_theme_graph.py`，传明确`--source-db`、`--database`并使用`--skip-vault`。没有题材库时，五模块仍可独立计算；不能伪造题材表后声称整份报告完成。

重新校准时仅使用2025分布与累计组内事件/样本。a=2/10使用主版本a=5的先验比例与锚点；`analyze.py`一次输出三条变体对照，不作参数择优。周期只读当日及过去正式分，后续走势仅用于事件结果和案例展示。

## 缺口补数与只读边界

生产计算默认只读，不联网。必要补数使用`data/backfill_live.py`，其写入路径必须与显式`--live-root`一致；令牌只由本机已有`coreClient.tushare_config`读取，不粘贴到命令、报告或Git。网络补数另需现有客户端依赖、账户权限和数据许可；免费报告路线不依赖这些条件。

```powershell
python policyStudy/policy/speculation_sentiment_lite/data/backfill_live.py --mode audit --repo . --data-root $sentimentData --live-root $sentimentData --audit-root "$sentimentRun\audit" --start 20241101 --end 20260930
python policyStudy/policy/speculation_sentiment_lite/data/backfill_live.py --mode backfill --repo . --data-root $sentimentData --live-root $sentimentData --audit-root "$sentimentRun\audit" --dates 20260910 --kinds daily,limit,suspend
```

这只是有界单日修复命令，不自动改变永久冻结设置，也不写冻结原库。处理空响应时分别保存已验证无事件与未验证缺失；补数返回成功后仍需查询行数并做完整覆盖审计。

要把本机新计算结果装配成报告输入，在完成上述覆盖审计后运行：

```powershell
py -3.12 -m venv .venv-sentiment-report
.\.venv-sentiment-report\Scripts\python.exe -m pip install -r docs/research/mrwu_sentiment_cycle_20261005/reproduction/requirements.txt
.\.venv-sentiment-calc\Scripts\python.exe docs/research/mrwu_sentiment_cycle_20261005/reproduction/assemble_data.py --formal "$sentimentRun\formal" --analysis "$sentimentRun\analysis" --audit "$sentimentRun\audit" --theme "$sentimentRun\theme" --output "$sentimentRun\report-data"
.\.venv-sentiment-report\Scripts\python.exe docs/research/mrwu_sentiment_cycle_20261005/reproduction/build_deck.py --data "$sentimentRun\report-data" --output "$sentimentRun\report.pdf"
```

题材从2025-12-24开始导出，是为年初案例保留前五个交易日背景；正文和统计仍限定2026年。新数据可能出现字体子集未包含的题材名称，此时用`--font`指定完整的免费Noto Sans SC兼容TTF，或按`make_font.py --help`重建字体子集。若本机缺B0旧快照，数据修正归因页会明确不具备该项复验；发布PDF中的415日基线证据随仓库保存。

## 版本、审计与解释

`data/calibration.json`给出固定先验、累计分子/分母、P10/P90、缺失数与校准指纹。`data/input_manifest.json`保存输入身份和哈希；完整本机审计保留原路径，发布版使用逻辑文件名。`data/clipping_statistics.csv`区分严格超界、等于端点、饱和与缺失。

`data/baseline_validation.json`证明相同旧输入415日的参考复现；`data/reference_to_formal_diff.csv`把补数、历史身份、日历/群体/质量修正和重新校准分开。正式数据变化不是算法复现失败，也不能被笼统解释成只有9/10、9/11改变。

`DELIVERY_MANIFEST.json`分别登记覆盖、数值、相位、PDF、复刻和Git验收。2026不是新盲测；题材标签首发与修订时刻仍未认证。关于收盘后的可用性约定及下一轮3个收益实验，分别见方法说明和[NEXT_EXPERIMENTS.md](NEXT_EXPERIMENTS.md)。
