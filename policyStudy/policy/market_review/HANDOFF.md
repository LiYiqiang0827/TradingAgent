# 复盘工具箱交接说明

这套工具用于收盘后的A股日复盘；多个已验收日包可以作为周复盘输入，但当前没有内建周聚合器。工具不负责自动下单，也不替用户决定真实仓位。

## 每天需要提供什么

1. 交易日，例如 `20260921`。
2. 本机已经配置好的 TradingAgent 数据环境与凭证；仓库不包含任何密钥。
3. 少量与当天主线相关的政策、公告或产业事件，写入 `CATALYSTS.json`。
4. 如使用GLM初稿，保存符合 schema 的 `GLM_VALIDATION.json`。

## 每天会得到什么

- 市场环境、成交额与宽度；
- 连续板梯队和N天M板；
- 昨日高标的晋级、断板和亏钱效应；
- 主要题材的宽度、梯队、核心和最早触板代表；
- 过去三周主线追踪；
- 首板后首次回调观察池、已完成再启样本和连续板接力池；
- 可供AI写作的压缩事实包；
- 最终报告的验收记录和哈希。

## 最短操作顺序

1. 运行 `build_review_packet.py`。
2. 确认 `PACKET_VALIDATION.json` 为 `PASS`，并阅读 `DATA_QUALITY.json`。
3. 补充 `CATALYSTS.json`，运行 `build_agent_packet.py`。
4. 让GLM生成初稿，或由GPT/Codex直接写作。
5. GPT/Codex按 `AI_CONTRACT.md` 实质复核。
6. 运行 `finalize_review_run.py --codex-reviewed` 留档。

## 出错时先看哪里

- 涨停数量或万科A一类状态异常：看 `limit_state_reconciliation`。
- 连续板/N天M板异常：看 `board_classification` 与逐日涨停历史。
- 最终回封时间缺失：不得用首次触板时间代替。
- 14:30后炸板缺失：说明分钟验证没有完成，不写成0。
- Agent写出数据包外的新数字：删除或提供独立证据。
- 没有合格候选：报告应明确空仓，不降低标准凑名单。

## 与题材涨停研究联动

如果要进一步研究某批候选的分钟走势或逐笔成交，先运行 `export_research_watchlist.py`。输出CSV兼容 `policyStudy/policy/题材涨停研究/scripts/data_gen.py`。

联动是可选的。日复盘不会自动触发大规模历史下载，历史研究结果也不能替代当日收盘事实包。

## 已知边界

- 部分股票开板后的最终回封时间可能不可得；
- 尾盘炸板需要分钟数据单独验证；
- active free float 只在最终少量候选上补查；
- 题材主线、资金迁移和可交易性仍需要GPT/Codex判断；
- 周复盘需要汇总一周内多个已验收的 `MR_PACKET.json`，当前未内建自动聚合器；
- 所有真实交易由用户决定。
