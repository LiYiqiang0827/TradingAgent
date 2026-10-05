# 大盘天气预报：独立人工标注

给标注者的材料在 packets/：cards.pdf 每日一张、cards.html 可离线打印，卡片仅10项中性事实及2025常见范围；另附 Harry 和 Li 各自的空标签、提名模板、小型参考表。请先分别标注，再比较。private_key/ 是机器答案、抽样依据和审计细节；两人独立标注完成前请勿打开或转发。

当前共15个唯一日期，已收到0个唯一提名。没有收到提名时仅为随机部分，仍需补充约15个记得清楚的日期。补齐入口：`python -m policyStudy.policy.market_sentiment.run human-pack --daily <日表> --output <新的human目录> --nominations <提名CSV>`。填过标签的目录不会被重建覆盖。
