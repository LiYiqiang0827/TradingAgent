# 大盘天气预报 v0.2

每天分别测量强势股挨打、延续、全市场活跃，按固定规则形成五种天气；经验转移频率用于次日描述性预报。旧 F1 五模块分原样保留为 `mkt_relay_score`。2025 是回顾性标尺期，2026 已被项目研究使用。入口不调用 C0、题材、策略收益或交易。

详细发现见 [报告](../../../docs/research/mkt_weather_v02_20261005/REPORT.md)，两条重建路线见 [复刻说明](../../../docs/research/mkt_weather_v02_20261005/REPRODUCE.md)。在仓库根运行：

```powershell
python -m policyStudy.policy.market_sentiment.run history --raw-daily docs/research/mkt_weather_v02_20261005/data/mkt_raw_daily.csv --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --calendar docs/research/mkt_weather_v02_20261005/data/market_calendar.csv --start 20250102 --end 20260930 --output <独立输出目录>
python -m policyStudy.policy.market_sentiment.run today --data-root '<安全停写的行情库目录>' --calibration docs/research/mkt_weather_v02_20261005/data/mkt_calibration_v02.json --output <持续派生目录>
```

手动 `today` 明示数据截止日；休市显示最新完成日。日历覆盖不足、上游仍写入或失败会明确标记；不得将旧快照称为今天。日表按日期重建后原子替换，不追加重复行；来源快照变更时保守重算历史和后继，记录历史差异。固定校准不随更新改变。

每日自动接入目前单独验收。必须由 22:00 上游任务**实际成功完成**后触发，使用互斥机制及匹配源文件状态的完成凭证；不能设置22:01猜测完成。数据统一任务尚未安全交接时，仅交付入口，不启用计划任务。

```powershell
python -m pytest policyStudy/policy/market_sentiment/tests -q
python -m policyStudy.policy.market_sentiment.run human-pack --daily <mkt_daily.csv> --output <human目录> --nominations <提名CSV>
python -m policyStudy.policy.market_sentiment.run evaluate-human --daily <mkt_daily.csv> --harry <Harry_labels.csv> --li <Li_labels.csv> --output <评估目录>
```

权重、阈值、a=5、2025线性分位与机器三档固定在 config/mkt_spec_v02.json；任何人工依据修订均需总控形成完整新版本，最多一次。空标签不通过验收，不代填人类意见。
