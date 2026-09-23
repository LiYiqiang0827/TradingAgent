# A 股每日复盘工具箱

## 1. 目标

工具箱把复盘拆成确定性事实与判断性结论：程序读取收盘行情、复核涨跌停状态、重建梯队、计算题材宽度和候选池；GPT/Codex 解释高标晋级、板块资金迁移、题材阶段、次日验证重点和是否空仓。

接口和压缩层负责省 token；GPT/Codex 直接写作负责避免“外部模型写一遍、主模型再重做一遍”的返工。

## 2. 默认架构

```text
coreClient.data_provider
        |
        v
build_review_packet.py
        +-- raw/*.csv
        +-- MR_PACKET.json
        +-- DATA_QUALITY.json
        +-- PACKET_VALIDATION.json
        |
        +-- CATALYSTS.json（仅政策/公告/产业事件）
        v
build_context_packet.py
        +-- CONTEXT_PACKET.json
        v
GPT/Codex 直接分析与写作
        +-- MR_YYYYMMDD.md
        v
finalize_review_run.py --codex-reviewed
        +-- RUN_ACCEPTANCE.json
        v
MiKTeX / XeLaTeX + latex/market_review_template.tex
        +-- 固定版式 PDF（运行产物不入库）
```

默认流程没有 GLM、Hermes 或 Gemini。例外审核条件见 `AI_CONTRACT.md`。

## 3. 命令

```powershell
$env:TRADING_AGENT_FROZEN = '0'
\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_review_packet `
  --trade-date 20260922 `
  --output-dir "C:\review_runs\20260922"

\.venv\Scripts\python.exe -m policyStudy.policy.market_review.build_context_packet `
  --packet "C:\review_runs\20260922\MR_PACKET.json" `
  --catalysts "C:\review_runs\20260922\CATALYSTS.json" `
  --output "C:\review_runs\20260922\CONTEXT_PACKET.json"

\.venv\Scripts\python.exe -m policyStudy.policy.market_review.finalize_review_run `
  --run-dir "C:\review_runs\20260922" `
  --report "C:\review_runs\20260922\MR_20260922.md" `
  --codex-reviewed

xelatex --enable-installer -interaction=nonstopmode -halt-on-error market_review.tex
```

已有快照默认复用；只有明确传入 `--refresh` 才重新请求。

## 4. 固定数据口径

- 沪深 A 股；排除北交所、ST、*ST、PT、退市整理和无交易股票。
- 开盘啦题材标签；一股多题材可重复计数。
- 连续板与 N 天 M 板分账。
- 一字板保留并标记难成交。
- 板块收盘封板率与触板封住率分别计算，不混用。
- 14:30 后炸板只有分钟数据完成验证时才报告。
- missing / not-ranked / zero 三种状态不互换。
- 不把“主力净流入”当作权威答案。
- active free float 仅对最终少量候选补查。

## 5. 可靠性闸门

### 5.1 涨跌停状态

来源列表的 `U/Z/D` 不是最终权威。程序用收盘价与每日涨跌停价复核，优先使用 `stk_limit`，缺失时才使用明确标注的后备计算。冲突必须留在 `DATA_QUALITY.json`。

### 5.2 连板重建

标签缺失时，根据逐日涨停历史重建连续板；非连续再板进入 N 天 M 板。不得把数字回退字段直接解释为连续板。

### 5.3 候选模式

- `first_pullback_in_progress`：首次回调仍在进行。
- `restart_completed_samples`：当日已完成再启，只作样本或延续观察。
- `continuous_board_relay`：连续板接力。
- `new_launches_for_future_tracking`：今日新启动，等待未来回调。

### 5.4 时间和缺失值

首次触板、最终回封、开板次数和尾盘炸板分别取证。最终回封缺失时不得拿首次触板替代；数据不可得时不得写零。

### 5.5 point-in-time

每次运行冻结接口快照。后来更新的题材标签、公告或成员关系不能改写历史交易日。

## 6. 产物

| 文件 | 作用 | GPT/Codex 使用方式 |
|---|---|---|
| `raw/*.csv` | 冻结接口快照 | 冲突时回查 |
| `MR_PACKET.json` | 完整事实包 | 按需深读 |
| `DATA_QUALITY.json` | 来源、缺失和修正 | 必须检查 |
| `PACKET_VALIDATION.json` | 确定性校验 | 必须 PASS |
| `CATALYSTS.json` | 定向催化研究 | 只补数据包外信息 |
| `CONTEXT_PACKET.json` | 压缩事实上下文 | 默认写作输入 |
| `RUN_ACCEPTANCE.json` | 最终哈希、字节数和验收结果 | 留档 |
| `MR_YYYYMMDD.md` | 已验收报告 | PDF 唯一文字源 |
| `*.pdf` | 固定版式交付 | 页面渲染后交付 |

旧 `AGENT_PACKET.json`、`GLM_VALIDATION.json` 和相应 schema 仅用于读取历史运行。

## 7. 联网搜索边界

允许：政策、会议、产业事件、公司公告、澄清与风险、最终候选的数据包外背景。

禁止重复搜索：已经验证的指数、成交额、涨停数量、连板梯队、晋级率、题材统计、个股当日价量。若确定性来源发生无法解释的冲突，先记录冲突，再作一次有目标的例外核验。

## 8. PDF 版式

`latex/market_review_template.tex` 定义 A4、字体、颜色、页眉页脚、标题层级、表格和引用框。使用 MiKTeX 的 XeLaTeX 编译。仓库不保存 LaTeX 软件、宏包或生成 PDF；排版阶段不能改写事实。

## 9. 与题材涨停研究的关系

`market_review` 负责当日/当周全市场结构；`policyStudy/policy/题材涨停研究` 负责历史样本、分钟 K 线和逐笔研究。通过 `export_research_watchlist.py` 传递候选 CSV，无运行时强依赖。历史研究结果不能替代当日事实包。
