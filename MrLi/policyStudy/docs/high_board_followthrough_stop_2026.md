# 高位连板后续走势、固定止损与数学复利

本组脚本以离线开盘啦涨停榜的精确 `N连板` 状态为准，按每轮行情首次达到4、5或6板的当天收盘建观察点。同轮晋级不重复计数；排除ST、ST摘帽、次新和北交所。2026年从较高板数开始可见、其起始板发生在2025年的轮次单列，不进入确切达板日主表。

`study_high_board_followthrough.py` 从 `coreClient.data_provider` 读取榜单和前复权日线，统计达板后1、2、3、5、10个市场交易日的收盘累计涨跌幅。`plot_high_board_return_distribution.py` 对4板后的2、3、5日结果画同尺度直方图。第N日未到、目标日无报价都保留状态而不向后挪动。

`study_high_board_fixed_stop.py` 在同一批轮次上计算固定-10%止损：以达板日收盘价为基准，从次一交易日开始逐日检查开盘价和最低价。开盘已跌穿止损价则按开盘价退出，其余触线按止损价退出；此后不重新买入。第2—5日分别统计首次止损或持有到该日收盘的累计收益。缺少中间日价格时，未止损路径记为不可评估。这里的价格触线成交只是乐观代理，封板买入、跌停卖出与订单队列没有验证，未扣费用。

截至2026-09-28的确切达板轮次为：4板175轮，5板63轮，6板26轮。固定止损后各窗口的有效样本、均值和中位数如下；括号中为有效样本数：

| 达板数 | 第2日 | 第3日 | 第4日 | 第5日 |
|---:|---:|---:|---:|---:|
| 4板 | +2.87% / +0.84%（171） | +2.57% / -1.76%（168） | +2.27% / -10.00%（164） | +2.29% / -10.00%（163） |
| 5板 | +3.67% / +3.31%（60） | +4.99% / +2.36%（58） | +4.93% / -1.15%（57） | +3.41% / -10.00%（56） |
| 6板 | +7.17% / +8.60%（24） | +6.89% / +7.95%（24） | +4.85% / +8.13%（23） | +2.30% / +1.63%（23） |

`study_high_board_naive_compounding.py` 将每笔可观察收益直接连乘，支持指定初始本金。这只是数学演示：4/5/6板轮次会同时出现多笔信号，不能每笔重复满仓使用同一账户资金；不同板数样本也有重叠。程序另外列出各退出窗口的最大重叠笔数以防误读。它不是现金/持仓账本，不得将数学连乘金额当成可执行复利曲线。

复现当前统计：

```bash
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_followthrough.py --board-threshold 4
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/plot_high_board_return_distribution.py --board-threshold 4
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_fixed_stop.py --board-threshold 4 --stop-pct 10
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_followthrough.py --board-threshold 5 --output-dir outputs/high_board_stop_comparison_2026/5_board
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_followthrough.py --board-threshold 6 --output-dir outputs/high_board_stop_comparison_2026/6_board
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_fixed_stop.py --board-threshold 5 --stop-pct 10 --input-dir outputs/high_board_stop_comparison_2026/5_board
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_fixed_stop.py --board-threshold 6 --stop-pct 10 --input-dir outputs/high_board_stop_comparison_2026/6_board
PYTHONPATH=. .venv/bin/python policyStudy/policy/题材涨停研究/scripts/study_high_board_naive_compounding.py --initial-capital 300000
```

生成的CSV、图和详细报告保存在 `outputs/`，不纳入Git仓库。研究结论只是历史描述；若要评估交易策略，需按当时可见数据确定候选、买入可达性、同时信号资金分配、卖出排队和成本，并在独立留出期或纸面执行中验证。
