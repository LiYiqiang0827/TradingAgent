# 复盘工具箱交接说明

这套工具用于收盘后的 A 股日复盘；周复盘聚合多个已验收日包。工具不下单，也不替用户决定仓位。

## 每日输入

1. 交易日，例如 `20260922`。
2. 本机配置好的 TradingAgent 数据环境；仓库不包含密钥。
3. 少量政策、公告或产业事件，写入 `CATALYSTS.json`。

## 每日输出

- 市场环境、成交与宽度；
- 连续板梯队和 N 天 M 板；
- 昨日高标晋级、断板和亏钱效应；
- 主要题材宽度、梯队、核心和可得的触板时间；
- 过去三周主线追踪；
- 四类候选池；
- 压缩的 `CONTEXT_PACKET.json`；
- 已验收 Markdown、固定版式 PDF 和 `RUN_ACCEPTANCE.json`。

## 最短操作顺序

1. 运行 `build_review_packet.py`。
2. 确认 `PACKET_VALIDATION.json` 为 `PASS`，阅读 `DATA_QUALITY.json`。
3. 只搜索催化和最终候选背景，生成 `CATALYSTS.json`。
4. 运行 `build_context_packet.py`。
5. GPT/Codex 直接分析和写作，不启动日常写作 Agent。
6. 运行 `finalize_review_run.py --codex-reviewed`。
7. 验收通过后，把内容填入 `latex/market_review_template.tex`，用 MiKTeX/XeLaTeX 编译并检查页面。

## 故障定位

- 涨停/炸板状态异常：看 `limit_state_reconciliation` 与收盘价/涨跌停价。
- 连续板或 N 天 M 板异常：看 `board_classification` 与逐日涨停历史。
- 最终回封时间缺失：写不可用，不得用首次触板替代。
- 14:30 后炸板缺失：分钟验证未完成，不写 0。
- 题材未入领先表：不等于零涨停或零成交。
- 没有合格候选：明确空仓，不降低标准凑名单。
- PDF 首次编译慢：通常是 MiKTeX 首次安装宏包；后续编译应明显加快。
- PDF 日志出现 `Overfull` 或 `Missing character`：修复模板/文本后重新生成，不交付。

## 已知边界

- 部分股票的最终回封时间、开板次数和尾盘炸板可能不可得。
- active free float 只在最终少量候选上补查。
- 题材主线、资金迁移和可交易性必须由 GPT/Codex 判断。
- 周复盘自动聚合器尚未内建。
- 精确 token 节省需要上游逐调用 usage 数据；文件字节数只能作为代理。
- 所有真实交易由用户决定。

## 历史兼容

`build_agent_packet.py` 与 GLM schema/示例没有删除，便于读取旧运行，但新运行使用 `build_context_packet.py` 和 `CONTEXT_PACKET.json`。除非符合 `AI_CONTRACT.md` 的例外条件，不创建外部模型任务。
