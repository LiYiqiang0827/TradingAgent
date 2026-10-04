# 接力情绪 F1 与探索性周期 C0

本目录提供2025固定标尺、2026历史环境测量的精简实现。先读[研究包](../../../docs/research/mrwu_sentiment_cycle_20261005/README.md)及[复刻说明](../../../docs/research/mrwu_sentiment_cycle_20261005/REPRODUCE.md)。不连接策略收益，不交易，不设置日更。

| 入口 | 职责 |
|---|---|
| `production.py` | 只读指定本机SQLite副本，历史身份与完整市场日群体，输出正式原始指标和五模块日表 |
| `score.py` | 固定2025先验/锚点、a=5主版本、a=2/10独立稳定性变体、严格截断统计 |
| `cycle.py` | 按冻结C0优先级计算相位、触发条件、历史轨迹与连续长度 |
| `theme_export.py` | 复用已存在的theme-market-review-v2历史接口，输出独立题材对照 |
| `baseline.py` | 以旧输入快照复算B0，与随附参考415日CSV逐项对账 |
| `data/backfill_live.py` | 显式LIVE边界内的有界审计/补数；复用本机已有Tushare客户端 |
| `data/restore_live_basic.py` | 为本次中断副本恢复保留的工具；不会覆盖已恢复的完整LIVE |

`config/formal_spec.json`和`config/CYCLE_SPEC.md`是方法合同。主序列要求五模块齐备；诊断字段`score_available`不能替代它。当前研究不强行构造缺少有效M6证据的六模块分。2026已被项目研究使用。

`baseline_reference/`保留原五脚本与小型对账CSV，仅作审计：原脚本中的临时目录不作为生产入口。`baseline.py`通过命令参数指定真实输入和输出。正式生产与复刻入口不依赖Harry的工作目录、Codex私有运行时或Office。

在仓库根目录运行核心测试：

```powershell
python -m pip install -r policyStudy/policy/speculation_sentiment_lite/requirements.txt
python -m pytest -q -p no:cacheprovider policyStudy/policy/speculation_sentiment_lite/tests
```

大型逐股事件和昨日群体明细保存到调用者指定的输出目录，不提交数据库或个股缓存。发布研究包含固定校准、日表、截断/质量/差异审计和可重建PDF的数据。
