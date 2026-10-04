# S01：live 副本恢复、明确日期补数与审计

这里的脚本不改变研究算法，不操作 Git，不修改 `ctrl` 断点，不初始化无关的 news 库。原库只用 `mode=ro&immutable=1` 读取；写入前验证解析后的路径都在显式 `--live-root`。令牌通过项目已有配置读取，输出只含是否存在。冻结开关只在当前 Python 子进程中切换，未修改永久环境变量。

所有路径均由参数指定。以下从 TradingAgent 仓库根目录运行；数据根目录由使用者配置，示例使用 `E:\TradingAgentData`，不要求原作者的机器路径或执行书：

```powershell
$repo = (Resolve-Path '.').Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
$scripts = Join-Path $repo 'policyStudy\policy\speculation_sentiment_lite\data'
$dataRoot = 'E:\TradingAgentData'
$source = Join-Path $dataRoot 'frozen'
$live = Join-Path $dataRoot 'live'
$run = Join-Path $dataRoot 'processed\sentiment_cycle_20261005_01'
$audit = Join-Path $run 'data_audit'
$start = '20241101'
$end = '20260930'

& $py "$scripts\restore_live_basic.py" --source-root $source --live-root $live --audit-root $audit
& $py "$scripts\backfill_live.py" --mode calendar --repo $repo --data-root $live --live-root $live --audit-root $audit --start $start --end $end
& $py "$scripts\backfill_live.py" --mode backfill --repo $repo --data-root $live --live-root $live --audit-root $audit --dates '20260910,20260918,20260921,20260922,20260923,20260924,20260928,20260929,20260930' --kinds 'daily,limit,suspend'
& $py "$scripts\backfill_live.py" --mode backfill --repo $repo --data-root $live --live-root $live --audit-root $audit --dates '20260917,20260918,20260921,20260922,20260923,20260924,20260928,20260929,20260930' --kinds 'kpl,limit_list,index'
& $py "$scripts\backfill_live.py" --mode backfill --repo $repo --data-root $live --live-root $live --audit-root $audit --kinds 'basic'
& $py "$scripts\backfill_live.py" --mode namechange --repo $repo --data-root $live --live-root $live --source-root $source --audit-root $audit
& $py "$scripts\s01_quality.py" --data-root $live --audit-root $audit --start $start --end $end
& $py "$scripts\backfill_live.py" --mode audit --repo $repo --data-root $live --live-root $live --audit-root $audit --start $start --end $end
& $py "$scripts\backfill_live.py" --mode manifest --repo $repo --data-root $live --live-root $live --source-root $source --audit-root $audit --start $start --end $end
```

复制入口先保留残件，再完整复制到临时文件，同时计算源 SHA256；副本单独算 SHA256、运行 SQLite 全库 `quick_check`，验证后替换标准库名。已有完成记录和标准 live 文件时默认退出，保留后来补入的数据；已有未确认残缺的标准库则拒绝覆盖。若已经验证的临时副本仅因 Windows 文件句柄导致改名失败，可在进程退出后以相同参数加 `--finalize-verified` 完成改名；此模式拒绝缺少哈希、完整性检查或源元数据不一致的恢复记录。

本次预热范围为 2024-11-01 至 2024-12-31，校准为完整2025年，正式分析至2026-09-30。恢复成功后可用 `complete_s01.py --repo $repo --live-root $live --source-root $source --run-root $run --start $start --end $end` 重跑基本数据补齐和审计；KPL/限价事件与指数使用上面独立补日命令。`--execution-brief` 是可选指纹参数，未提供时明确记录未提供。

`backfill_live.py` 按显式日期调用底层 API，用项目现有 `upsert_df` 的 `INSERT OR IGNORE` 入库，并立即重复插入同一响应核验幂等性。分页页数、offset、行数和响应哈希写入 `request_checkpoints.jsonl`。成功空响应与请求失败分别记录；官方日历确定开市日，不把休市日的空响应当成数据缺口。`daily_basic` 不参与五模块计算，只在实际需要时通过 `--kinds daily_basic` 补。

历史名称先做全局分页并探测空尾页，再做固定单股抽查、缺失身份和研究期有效区间核验。实测单股接口可补充全局接口遗漏的区间，因此不能只凭空尾页宣称全历史完整。若质量表列出研究期缺口，用同一入口加 `--repair-codes-from <缺口CSV>`，只请求 CSV 的 `ts_code`；加 `--download-only` 可只更新审计缓存、随后再单独加载。

原始表为 `tbl_cn_namechange`：`ts_code, name, start_date, end_date, ann_date, change_reason, snap_ts, raw_record_hash`。原始日期保留 `YYYYMMDD`，审计 CSV 用 ISO 日期。完全重复的原始记录按六个来源字段 SHA256 去重；区间冲突保留。有效区间包含起止日，空结束日期表示开放区间；共享边界优先较晚 `start_date`，并保留歧义标记；同一较晚开始日仍有 ST 状态冲突时需标记降级。公告日期缺失保持空，不推测发布时间。

`data_coverage.csv` 按官方日历列出每天每表行数；`market_code_coverage.csv` 和 `namechange_daily_coverage.csv` 使用实际沪深日线代码作为分母。`namechange_quality.csv` 列出原始区间的无效日期、开放区间、重叠、共享边界和缺失公告日期。`input_manifest.json` 保存 quiescent live 文件哈希、源库证据和输入范围。
