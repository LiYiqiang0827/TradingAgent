# Market Review - A 股复盘工具箱

把 `coreClient.data_provider` 的统一接口整理成紧凑、可复算、可由 GPT/Codex 直接分析的收盘复盘事实包。模型不读取全市场原始表，也不重复联网搜索已验证的价格、涨停数量、连板梯队和题材统计。

## 文档入口

| 文档 | 内容 |
|---|---|
| [NO_AGENT_HANDOFF.md](NO_AGENT_HANDOFF.md) | 默认无外部 Agent 的决策、2026-09-22 复盘复盘、平台修正和 LaTeX 交接 |
| [AI_CONTRACT.md](AI_CONTRACT.md) | GPT/Codex 的不可替代价值、角色边界和最终验收清单 |
| [REPORT_CONTRACT.md](REPORT_CONTRACT.md) | 高标晋级、资金迁移和首次回调再启的复盘方法 |
| [TOOLBOX.md](TOOLBOX.md) | 架构、命令、数据口径、产物和题材研究联动 |
| [HANDOFF.md](HANDOFF.md) | 每日最短操作路径和故障定位 |
| [toolbox_manifest.json](toolbox_manifest.json) | AI/自动化程序的机器可读入口 |
| [AGENTS.md](AGENTS.md) | 本目录强制执行规则 |

第一次接手的 AI 必须按 `toolbox_manifest.json.reading_order` 阅读。

## 默认工作流

```text
确定性数据包 -> 确定性校验 -> 定向催化搜索 -> 压缩上下文
             -> GPT/Codex 直接分析与写作 -> 最终验收 -> XeLaTeX PDF
```

日常流程不调用 GLM、Hermes、Gemini 或其他写作/审核 Agent。历史 GLM schema 和 `build_agent_packet.py` 为兼容旧运行保留，不属于默认路径。

## 固定规则

- 股票池：沪深 A 股，排除北交所、ST、*ST、PT、退市整理和无交易股票。
- 题材使用开盘啦标签；同一股票可在多个题材重复计数。
- 连续板进入梯队；N 天 M 板单独展示。
- 一字板保留，但提示不可交易风险。
- 板块收盘封板率 = 收盘涨停数 / 合格板块成员数。
- 触板封住率 = 收盘涨停数 /（收盘涨停数 + 触板未封数）。
- 涨停列表状态再与收盘价和涨跌停价复核。
- 连板连续性用逐日涨停历史验证。
- 14:30 后炸板必须有分钟证据，否则写不可用。
- 缺失、未进入排名和数值零严格分开。
- 不把“主力净流入”当作权威资金迁移。
- active free float 不进入主流程，只在最终少量候选确有必要时补查。
- 候选分成首次回调进行中、已完成再启、连续板接力和今日新启动。

## 快速运行

在 TradingAgent 仓库根目录执行：

```powershell
$env:TRADING_AGENT_FROZEN = '0'
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_review_packet `
  --trade-date 20260922 `
  --output-dir "C:\review_runs\20260922"
```

准备少量 `CATALYSTS.json` 后生成压缩上下文：

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_context_packet `
  --packet "C:\review_runs\20260922\MR_PACKET.json" `
  --catalysts "C:\review_runs\20260922\CATALYSTS.json" `
  --output "C:\review_runs\20260922\CONTEXT_PACKET.json"
```

GPT/Codex 直接写完并实质复核后留档：

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.finalize_review_run `
  --run-dir "C:\review_runs\20260922" `
  --report "C:\review_runs\20260922\MR_20260922.md" `
  --codex-reviewed
```

验收通过后，由 AI 把报告内容填入 `latex/market_review_template.tex`，使用 MiKTeX 的 XeLaTeX 编译：

```powershell
xelatex --enable-installer -interaction=nonstopmode -halt-on-error market_review.tex
```

仓库只保存 `.tex` 模板，不保存 MiKTeX、宏包缓存、生成 PDF 或其他运行产物。

## 与题材涨停研究联动

`export_research_watchlist.py` 可把候选转为 `policyStudy/policy/题材涨停研究/scripts/data_gen.py` 可读取的 CSV。它只用于后续分钟线/逐笔历史研究，不是日复盘运行时强依赖，也不能用后来标签改写历史快照。

## 测试

```powershell
\.venv\Scripts\python.exe -m unittest policyStudy.policy.market_review.test_market_review -v
```

示例 JSON 只展示结构，不包含可用于真实复盘的行情事实。运行目录、原始快照和生成 PDF 不提交到 Git；仓库保存代码、规则、模板、schema 和示例。
