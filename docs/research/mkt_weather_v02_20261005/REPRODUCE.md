# 复刻与日更

仓库根运行，Python 3.12，使用免费包 numpy、pandas、pyarrow、matplotlib；测试另需 pytest。随附派生表路线不需要数据库、联网、令牌或模型。Windows 现有环境为 `C:\Project Rocket\Codex\TradingAgent\.venv\Scripts\python.exe`。

## 路线一：从随附派生小表复刻

```powershell
python -m policyStudy.policy.market_sentiment.run history --raw-daily docs/research/mkt_weather_v02_20261005/data/mkt_raw_daily.csv --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --calendar docs/research/mkt_weather_v02_20261005/data/market_calendar.csv --start 20250102 --end 20260930 --generated-at 2026-10-05T00:00:00+00:00 --output C:/ProjectRocketWeatherRebuild
python -m policyStudy.policy.market_sentiment.figures --daily C:/ProjectRocketWeatherRebuild/mkt_daily.csv --font docs/research/mkt_weather_v02_20261005/reproduction/MktSansSC.ttf --output C:/ProjectRocketWeatherRebuild
```

读数、天气、逐日预报、评价、截断和图表可全部重建。运行时间与生成时间、输入路径身份允许不同，计算值必须一致；每日表的 input_snapshot_id 区分读入已发布小表和原始事件的两条溯源路线。生产校准一直复用同一个文件。完全重新拟合2025标尺时去掉 `--calibration`，用完整424日小表；值应相同，但校准所记录的输入指纹随溯源路线变化。

## 路线二：从本地稳定 F1 事件或行情库计算

本轮使用 `D:\ProjectRocketData\processed\TradingAgent\outputs\speculation_sentiment\sentiment_cycle_20261005_01\formal_corrected` 的 events.parquet、cohorts.parquet、sentiment_daily.csv。发布 F1 日表与该目录日表的 SHA256 完全一致；错误的早期 formal 目录不作为正式事件来源。逐股明细留在本机，不入 Git。

```powershell
python -m policyStudy.policy.market_sentiment.run history --formal-dir '<formal_corrected目录>' --index-daily docs/research/mkt_weather_v02_20261005/data/index_daily.csv --calendar docs/research/mkt_weather_v02_20261005/data/market_calendar.csv --start 20250102 --end 20260930 --output '<新RUN目录>'
python -m policyStudy.policy.market_sentiment.run history --data-root 'D:/ProjectRocketData/live' --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --start 20250102 --end 20260930 --output '<另一个新RUN目录>'
```

行情路线只读复用未改动的 F1 production/score/formal_spec 与旧固定校准。2024-11-01起既有预热用于前20市场日均额和昨日群体，没有更早数据下载。数据库必须无非空 WAL/journal，且读取前后文件状态一致。首次需读取历史及验证来源，后续以源状态检查复用缓存；历史修订重算后继，不拼接旧规则和新规则。

统一后的总库路径为 `D:/ProjectRocketData/legacy_C_Project_Rocket/Trading Agent Database`。只有独立数据统一任务完成旧F1的181日直接原库复算对账后才切换；不能对正在合并中的原库执行本路线。

## 今天与自动接入

```powershell
python -m policyStudy.policy.market_sentiment.run today --data-root '<已验证且停写的总库>' --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --output '<持续天气输出目录>' --upstream-receipt '<上游实际完成凭证.json>'
```

`today` 锁住派生目录，最新日由行情和限价共同截止日确定；当前交易日15:00前拒绝；日历不足显示确切状态。数据源、校准或上游完成凭证不匹配则报错。完成凭证格式见 [自动接入合同](acceptance/DAILY_AUTOMATION.md)。未挂接时不要把手动重跑成功当自动运行成功，节后首日无人干预成功另记。

## 人工标注

只把 human/packets 内容发给 Harry 与 Li。每人独立填写各自CSV。补提名在 `nominations_template.csv` 的 trade_date 列填约15个日期后运行 human-pack；程序按种子20261005重建分层随机部分，重合日期按同天气候选补抽。不要向标注者展示 private_key。程序不把既有聊天复盘当两人的正式标签。

```powershell
python -m policyStudy.policy.market_sentiment.run human-pack --daily docs/research/mkt_weather_v02_20261005/data/mkt_daily.csv --nominations '<nominations.csv>' --output '<新的human目录>'
python -m policyStudy.policy.market_sentiment.run evaluate-human --daily docs/research/mkt_weather_v02_20261005/data/mkt_daily.csv --harry '<Harry标签CSV>' --li '<Li标签CSV>' --output '<人工评估目录>'
```

分维度报告机器对两人、两人相互一致率，以及双方一致日中机器的准确率。三项均≥70%才通过；分母零待标注。未达标只允许一次有人工证据的切点修订；不调整指标、锚点、先验、权重，不按收益择优。

## 验收入口

```powershell
python -m pytest policyStudy/policy/market_sentiment/tests -q
python -m policyStudy.policy.market_sentiment.acceptance --formal-dir '<formal_corrected>' --published-daily docs/research/mrwu_sentiment_cycle_20261005/data/formal_daily_2025_2026.csv --daily docs/research/mkt_weather_v02_20261005/data/mkt_daily.csv --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --calendar docs/research/mkt_weather_v02_20261005/data/market_calendar.csv --output '<独立验收目录>'
```

实际前缀是先截断事件、群体、旧日表，再重跑新增计算；不是裁剪结果CSV。2025全年固定锚点的事后性明确保留。验收记录与源码/输入清单见 acceptance，状态见 STATUS.json。

如果本机系统 TEMP 的 pytest 目录无写权限，可为pytest加 `--basetemp <已创建父目录下的全新子目录>`。本轮实际验证为32项测试加8个边界子用例，JUnit记录40条全部通过。手动LIVE入口从通过文件状态校验的既有原始帧缓存完成端到端计算，后续同日复跑约1秒；源数据修订后的完整读取耗时取决于磁盘和数据规模，未将缓存复跑时间说成冷读性能。
