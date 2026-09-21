# Market Review - A股复盘工具箱

把 `coreClient.data_provider` 的统一接口整理成紧凑、可复算、可交给AI写作的收盘复盘事实包。模型读取事实包，不读取全市场原始表，也不重复联网搜索价格、涨停数量、连板梯队和题材统计。

## 文档入口

| 文档 | 适合谁 | 内容 |
|---|---|---|
| [TOOLBOX.md](TOOLBOX.md) | 使用者、维护者 | 完整架构、运行流程、数据口径、与题材研究的关系 |
| [REPORT_CONTRACT.md](REPORT_CONTRACT.md) | 写作模型、复盘人员 | 高标晋级、资金迁移和首次回调再启的复盘方法 |
| [AI_CONTRACT.md](AI_CONTRACT.md) | Agent、GPT/Codex | 角色边界、GPT不可替代价值、最终验收清单 |
| [HANDOFF.md](HANDOFF.md) | 新使用者 | 每日输入输出、最短操作路径、故障定位和已知边界 |
| [toolbox_manifest.json](toolbox_manifest.json) | AI和自动化程序 | 机器可读入口、产物、命令与硬约束 |
| [AGENTS.md](AGENTS.md) | 仓库内Agent | 本目录强制执行规则 |

第一次接手本工具箱的AI应按 `toolbox_manifest.json` 中的 `reading_order` 阅读。

## 固定规则

- 股票池：沪深A股，排除北交所、ST、*ST、PT、退市整理和无交易股票。
- 题材：使用开盘啦标签；同一股票可在多个题材重复计数。
- 连续板进入梯队；N天M板单独展示。
- 一字板保留，但提示不可交易风险。
- 板块收盘封板率 = 收盘涨停数 /（合格板块成员数）。
- 触板封住率 = 收盘涨停数 /（收盘涨停数 + 触板未封数）。
- 涨停列表状态必须再与收盘价及涨跌停价复核。
- 连板连续性必须用逐日涨停历史验证，不能只信缺失标签的数字回退。
- 14:30后炸板必须有分钟数据证据，否则写不可用。
- 核心程序不批量估算 active free float。
- 不把“主力净流入”当作权威资金迁移。
- 候选分成首次回调进行中、已完成再启、连续板接力和今日新启动。

## 快速运行

在 TradingAgent 仓库根目录执行：

```powershell
$env:TRADING_AGENT_FROZEN = '0'
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_review_packet `
  --trade-date 20260921 `
  --output-dir "C:\review_runs\20260921"
```

会生成：

- `raw/*.csv`：冻结接口快照；
- `MR_PACKET.json`：完整事实包；
- `DATA_QUALITY.json`：来源、缺失与修正；
- `PACKET_VALIDATION.json`：确定性校验结论。

已有快照默认复用；只有明确传入 `--refresh` 才重新请求。

## 受限写作流程

先按 [schemas/catalysts.schema.json](schemas/catalysts.schema.json) 准备少量 `CATALYSTS.json`，只放政策、公告和产业事件，不重复行情事实。

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_agent_packet `
  --packet "C:\review_runs\20260921\MR_PACKET.json" `
  --catalysts "C:\review_runs\20260921\CATALYSTS.json" `
  --output "C:\review_runs\20260921\AGENT_PACKET.json"
```

GLM可以根据 `AGENT_PACKET.json` 生成受限初稿，但初稿没有事实权。GPT/Codex必须检查数据修正、题材结论、资金迁移和候选模式后再接受。

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.finalize_review_run `
  --run-dir "C:\review_runs\20260921" `
  --report "C:\review_runs\20260921\MR_20260921.md" `
  --glm-validation "C:\review_runs\20260921\GLM_VALIDATION.json" `
  --codex-reviewed
```

日常复盘不使用 Gemini。独立审核只用于策略/风控规则变化、重大真实交易决策或无法解决的数据冲突。

## 导出到题材涨停研究

复盘候选可以转成 `policyStudy/policy/题材涨停研究/scripts/data_gen.py` 可读取的 watchlist：

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.export_research_watchlist `
  --packet "C:\review_runs\20260921\MR_PACKET.json" `
  --include-pullbacks `
  --output "C:\review_runs\20260921\research_watchlist.csv"
```

这只是可选桥接，不是运行时强依赖。`market_review` 负责当日判断；`题材涨停研究` 负责历史样本、分钟线和逐笔研究。

## 测试

```powershell
\.venv\Scripts\python.exe -m unittest policyStudy.policy.market_review.test_market_review -v
```

示例JSON只展示结构，不包含可用于真实复盘的行情事实。
