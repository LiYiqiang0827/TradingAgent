# A股每日复盘工具箱

## 1. 工具箱解决什么问题

本工具箱把每日复盘拆成两类工作：

1. 确定性工作：读取收盘行情、复算涨跌停状态、生成连板梯队、题材宽度、晋级与候选池。
2. 判断性工作：解释高标晋级、板块资金迁移、题材阶段、次日验证重点，以及是否应当空仓。

目标不是让模型重新搜索全市场，而是让程序先生成可审计的事实包，再让模型处理必须推理的部分。

## 2. 总体流程

```text
coreClient.data_provider
        |
        v
build_review_packet.py
        |
        +-- MR_PACKET.json            完整事实包
        +-- DATA_QUALITY.json          数据来源、缺失与修正
        +-- PACKET_VALIDATION.json     确定性校验结果
        |
        v
CATALYSTS.json                         少量政策/公告/产业催化
        |
        v
build_agent_packet.py
        |
        +-- AGENT_PACKET.json         压缩后的写作输入
        |
        v
受限写作 Agent（GLM，可选）
        |
        +-- 未验证初稿
        |
        v
GPT/Codex 实质复核与最终写作
        |
        +-- 每日复盘 Markdown / PDF
        +-- RUN_ACCEPTANCE.json
```

日常流程不使用 Gemini 独立审核。外部独立审核只保留给策略规则变化、真实重大交易决策或无法消解的数据冲突。

## 3. 最小运行方式

在 TradingAgent 仓库根目录执行：

```powershell
$env:TRADING_AGENT_FROZEN = '0'
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_review_packet `
  --trade-date 20260921 `
  --output-dir "C:\review_runs\20260921"
```

准备少量催化事实后压缩模型输入：

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_agent_packet `
  --packet "C:\review_runs\20260921\MR_PACKET.json" `
  --catalysts "C:\review_runs\20260921\CATALYSTS.json" `
  --output "C:\review_runs\20260921\AGENT_PACKET.json"
```

完成写作和 GPT/Codex 实质复核后记录验收：

```powershell
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.finalize_review_run `
  --run-dir "C:\review_runs\20260921" `
  --report "C:\review_runs\20260921\MR_20260921.md" `
  --glm-validation "C:\review_runs\20260921\GLM_VALIDATION.json" `
  --codex-reviewed
```

## 4. 固定数据口径

- 股票池：沪深A股，排除北交所、ST、*ST、PT、退市整理、无交易股票。
- 题材归属：开盘啦标签；同一股票可以在多个题材重复计数。
- 连续板与N天M板分开，N天M板不进入连续梯队。
- 一字板保留，但必须提示不可交易或难成交风险。
- 板块收盘封板率：板块内收盘涨停数 / 板块合格成员数。
- 触板封住率：收盘涨停数 /（收盘涨停数 + 触板未封数）。
- 14:30后炸板只有在分钟数据完成验证时才报告，否则写不可用。
- 不把“主力净流入”当作资金迁移的权威答案。
- active free float 不由核心程序批量估算，只允许在最终少量候选上补查。

## 5. 可靠性闸门

### 5.1 涨跌停状态复核

涨停列表的 `U/Z/D` 不是最终权威。程序会用收盘价与每日涨跌停价复核。优先使用官方 `stk_limit` 数据；缺失时，使用明确标注的前收盘价和板块涨跌幅规则后备计算。

### 5.2 连板重建

当开盘啦 `status` 缺失时，不允许把数字字段直接当作连续板。程序会根据逐日涨停历史重建连续板，并把非连续再板放入N天M板列表。

### 5.3 候选模式分账

候选被拆成四个池：

- `first_pullback_in_progress`：首板后首次回调仍在进行，可作为前置观察池。
- `restart_completed_samples`：当日已经完成再启，只能作为形态样本或延续观察。
- `continuous_board_relay`：连续涨停接力，不冒充首次回调再启。
- `new_launches_for_future_tracking`：今日新启动，留待未来回调跟踪。

### 5.4 时间字段

首次触板时间、是否开板、最终回封时间必须分开。最终回封时间缺失时不得用首次触板时间代替。

## 6. 输出文件

| 文件 | 作用 | 是否给模型 |
|---|---|---|
| `raw/*.csv` | 冻结接口快照 | 否 |
| `MR_PACKET.json` | 完整复盘事实包 | GPT按需读取 |
| `DATA_QUALITY.json` | 来源、缺失、修正、历史覆盖 | GPT必须检查 |
| `PACKET_VALIDATION.json` | 确定性校验结论 | 必须为PASS |
| `CATALYSTS.json` | 目标化催化研究 | 是 |
| `AGENT_PACKET.json` | 压缩的受限写作输入 | 给写作Agent |
| `GLM_VALIDATION.json` | 写作Worker的格式验证 | 给最终验收器 |
| `RUN_ACCEPTANCE.json` | 最终验收记录与哈希 | 留档 |

`raw/` 和每次运行目录不应提交到 Git；仓库只保存代码、规则、示例和 schema。

## 7. 与“题材涨停研究”的关系

`market_review` 与 `policyStudy/policy/题材涨停研究` 是相邻工具，不是强依赖：

- `market_review` 负责单日/单周的全市场收盘复盘与候选筛选。
- `题材涨停研究` 负责历史样本、分钟K线和逐笔数据的进一步研究。
- 两者共享开盘啦题材与涨停语义，但日复盘不能直接把历史 watchlist 当作当日 point-in-time 事实。

如需继续研究候选，可用 `export_research_watchlist.py` 把 `MR_PACKET.json` 中的候选导出为兼容 watchlist，再交给：

```powershell
python policyStudy/policy/题材涨停研究/scripts/data_gen.py <watchlist.csv> --only minute
```

默认不会自动触发大规模分钟或逐笔下载，避免日常复盘和历史研究互相拖慢。

## 8. AI接手时的阅读顺序

1. `toolbox_manifest.json`
2. `AI_CONTRACT.md`
3. `REPORT_CONTRACT.md`
4. `README.md`
5. 当次运行目录中的 `PACKET_VALIDATION.json`、`DATA_QUALITY.json`、`AGENT_PACKET.json`

AI不得把文档中的示例日期、股票或数字当作当前行情。
