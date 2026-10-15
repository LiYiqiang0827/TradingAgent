# 可借鉴的研究和项目

## 学术研究

1. [Lo、Mamaysky、Wang：Foundations of Technical Analysis](https://web.mit.edu/wangj/www/pap/LoMamayskyWang00.pdf) 用非参数核回归把人工图形识别转成可复现规则，并比较形态出现后的条件收益与无条件收益。可借鉴点：理论标签必须落到确定性的识别器和样本外统计。
2. [Brock、Lakonishok、LeBaron：Simple Technical Trading Rules and the Stochastic Properties of Stock Returns](https://doi.org/10.1111/j.1540-6261.1992.tb04681.x) 使用移动平均和区间突破规则，并通过bootstrap检验结果。可借鉴点：五套理论至少要与简单突破/均线基线比较。
3. [PLOS ONE：中国股市价格形态识别研究](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0255558) 将传统价格形态形式化，在大规模中国股票日线中按时间切分训练和验证。可借鉴点：使用A股本地样本、严格时间外验证、保留形态识别与收益检验两层。
4. [Stock market prediction with deep learning: case of China](https://doi.org/10.1016/j.frl.2021.102209) 探索将K线图像和基本面信息用于中国股票预测。可借鉴点：图像是补充输入，不能替代精确的结构化OHLCV。

## 开源结构识别

1. [CZSC](https://github.com/waditu/czsc) 提供分型、笔、多级别信号、回放和交易评估等工程实现。可借鉴点：缠论必须固定算法版本和级别，生成可回放中间结构，而不是让语言模型凭图自由命名。
2. [WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent) 把主线发现、量价事件、Wyckoff候选和执行治理分层。其[方法说明](https://github.com/YoungCan-Wang/WyckoffTradingAgent/wiki/02_Finance_Wyckoff_Method)公开了哪些信号在该项目自己的数据和回测口径中启用或停用；这些收益数字不能直接外推到TradingAgent。可借鉴点：确定性规则先产生候选，AI负责解释和审计；每类事件单独做收益归因，表现差的教科书信号应降权或停用。

## 金融智能体

1. [TradingAgents论文](https://arxiv.org/abs/2412.20138)及其[开源代码](https://github.com/TauricResearch/TradingAgents)采用角色分工、看多看空辩论、风险讨论和历史反思。可借鉴点：强制生成反证与备选路径，并保存决策日志供事后复盘。
2. [FinMem](https://arxiv.org/abs/2311.13743)使用分层记忆组织不同时间尺度的金融信息。可借鉴点：事实、事件、结构判断和长期经验分层保存，避免复盘结论污染原始数据。
3. 2026年预印本[Do VLMs Truly Read Candlesticks?](https://arxiv.org/abs/2604.12659)专门评估视觉语言模型读取K线的能力与限制。可借鉴点：模型对持续趋势更容易，对复杂常见场景和预测周期变化更脆弱，因此数值提取应确定化、图像用于复核。

## 对TradingAgent的落地建议

1. 先用确定性程序生成事实和候选事件，再让模型做解释、反证和冲突裁决。
2. 缠论固定CZSC或项目自有算法版本；波浪固定摆动点算法和参数，同时保留备选计数。
3. 每个历史样本冻结 `as_of` 数据包、提示词、模型输出，再读取未来结果。
4. 将同一根K线衍生出的五种术语合并为同源证据，防止伪共识。
5. 逐项做消融。某理论若没有样本外增益，就保留为解释框架而不提高评分权重。
6. 只对题材研究技能筛出的候选运行深度K线分析，减少噪声和计算成本。
