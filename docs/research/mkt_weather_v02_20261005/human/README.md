# 大盘天气预报：独立人工标注

给标注者的材料在 packets/，包括按日期排序的中性事实、Harry 和 Li 各自的空标签，以及提名模板。请先分别标注，再比较。private_key/ 是机器答案与抽样依据；两人独立标注完成前请勿打开或转发。

当前共15个唯一日期，已收到0个唯一提名。没有收到提名时仅为随机部分，仍需补充约15个记得清楚的日期。补齐入口：`python -m policyStudy.policy.market_sentiment.run human-pack --daily <日表> --output <新的human目录> --nominations <提名CSV>`。填过标签的目录不会被重建覆盖。
