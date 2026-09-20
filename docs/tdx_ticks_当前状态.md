# TDX ticks 数据接口 — 当前状态档位

> 快照日期:**2026-09-19**(北京时间)
> 目的:把"修复到什么程度 + 没修什么 + 下一步可选路径"固化下来,供后续维护者/评审者一眼看明白当前真实状态,而不是凭印象。
> 配套文档:`docs/落库方案_v2.md`(决策记录)、`coreClient/docs/tdx_client.md`(接口文档)、`scripts/tdx_matrix_validation_v1.json`(矩阵验收原始数据)

---

## 1. 一句话状态

**接口层面已修好(新接口 + 18 单测 + 24-cell 矩阵全绿);但生产 db 与调用路径仍是修复前状态,因为用户没有触发迁移脚本。**

---

## 2. 接口层(代码)

### 2.1 两个调用入口

| 接口 | 文件 | 修复状态 | 测试覆盖 |
|---|---|---|---|
| `TdxClient.get_history_ticks(ts_code, date)` | `coreClient/tdx_client.py:361` | **未修复**(旧路径完全保留) | 无新增 |
| `fetch_history_ticks_with_meta(client, ts_code, date)` | `coreClient/tdx_ticks_meta.py:95`(新模块) | **已修复** | 18 个 unittest 全绿 |

### 2.2 已修的 3 个 wrapper bug(只在 `fetch_history_ticks_with_meta` 生效)

| # | 问题 | 修复点 | 证据 |
|---|---|---|---|
| 1 | `seen` key 缺 `buyorsell`,同价位同手数但 buy/卖方向不同的真成交被吞 | `tdx_client.py:446` + `tdx_ticks_meta.py:182` 的 seen key 改成 4 元组 | 矩阵验收:每 cell 救回 13 笔/天/股 |
| 2 | `_enrich_ticks` 给 wrapper 自产 `seqId`(全局累加),重抓时 PK 错位 → DB 永远指向旧数据 | `_enrich_ticks` 改成给 `seqId_in_minute`(同分钟内自增);旧 `seqId` 字段被 `pop()` 删除 | 3 个 seqId_in_minute 专项单测 |
| 3 | 单页失败/截断时静默返回部分数据,调用方无感知 | 新增 `TicksFetchMeta`(raw_rows / returned_rows / termination / pagination_complete);`is_safe_to_persist()` gate;`IncompleteDataError` | 5 个 gate 单测 + 4 个 termination 单测 |

### 2.3 未修的 2 个 wrapper 问题

| # | 问题 | 当前行为 | 决策 |
|---|---|---|---|
| 4 | 6 台主机 `random.choice` 随机切,无法表达"优先 A/B 组" | 每次连接随机抽 | **用户决定不动**(不属于"数据正确性",只属于"可靠性") |
| 5 | `buyorsell=5` 含义未知 | 原样透传,不映射/不删除/不加标注 | **用户决定保持现状 + 文档化 TODO**(见 § 4) |

### 2.4 服务端实测行为(2026-09-19 矩阵)

| 组 | 服务器 | buyorsell 返回 | 盘前零量 | 累计 vol(vol>0) |
|---|---|---|---|---|
| **A 组** | 180.153.18.170 / 60.12.136.250 / 115.238.56.198 | `{0, 1, 2, 5, 8}` 全给 | 14 条盘前 | 与 B 组一致 |
| **B 组** | 123.125.108.14 / 218.6.170.47 / 123.125.108.90 | `{0, 1, 2, 5}` 过滤 8 | 1 条盘前 | 与 A 组一致 |

**关键发现**: 两组**累计 vol 完全一致**,只是 B 组清洗过(过滤 buyorsell=8 + 盘前零量)。A 组保留全部原始数据。

---

## 3. 持久化层(schema)

### 3.1 两个表状态(2026-09-19 实测)

| 表 | 存在? | 行数 | PK | 用途 |
|---|---|---|---|---|
| `tbl_tick` | ✅ 存在 | 0 行 | `(ts_code, trade_date, seqId)` | 旧落库目标(wrapper 自产 seqId,有错位风险) |
| `tbl_tick_ctrl` | ✅ 存在 | 0 行 | `(ts_code, trade_date)` | 旧下载状态 |
| `tbl_tick_v2` | ❌ **不存在** | — | `(ts_code, trade_date, time, seqId_in_minute)` | 设计方案已落,未实际建 |
| `tbl_tick_v2_ctrl` | ❌ **不存在** | — | `(ts_code, trade_date)` | 设计方案已落,未实际建 |

**重要**: `policy_ticks.db` 当前**和修复前一模一样**。

### 3.2 代码改动(已落地,等触发)

- `coreClient/offlineDataManager/scripts/core/offline_db_client.py`:`KIND_SCHEMAS` 加 `"ticks_v2"` 完整 schema
- `INT_COLS` 加 `seqId_in_minute / fetch_seq / fetch_complete`;`TEXT_COLS` 加 `fetched_at / fetched_host`
- 新增 helper `table_exists(db_path, table_name)` 用于双写兼容检测
- `coreClient/offlineDataManager/scripts/core/offline_downloader.py`:`update_ticks` 双写兼容
  - 检测到 `tbl_tick_v2` 存在 → 走新逻辑(新接口 + gate + 4 审计字段)
  - 检测到不存在 → 走旧逻辑(完全不变)
- `scripts/migrate_tbl_tick_v1_to_v2.py`:迁移脚本(默认 dry-run,加 `--execute` 才动 db)

### 3.3 双写兼容的现状行为

| `tbl_tick_v2` 存在? | `update_ticks` 走的路径 | 结果 |
|---|---|---|
| ❌(当前) | **旧路径**(完全不变) | 与修复前完全一致,无影响 |
| ✅(未来) | 新路径(新接口 + gate + 审计字段) | 数据可靠性提升,但破坏 `seqId` 字段 |

---

## 4. buyorsell 字段权威定义

| 值 | 含义 | 来源等级 |
|---|---|---|
| `0` | 买盘(主动买入) | 一手文档:`xmtdx` + `easy_tdx` |
| `1` | 卖盘(主动卖出) | 一手文档:`xmtdx` + `easy_tdx` |
| `2` | 中性/撮合 | 一手文档:`xmtdx` + `easy_tdx` |
| `8` | 集合竞价(开盘/收盘前) | 一手文档:`xmtdx` + `easy_tdx` |
| `5` | **未知,待挖掘** | 推断(无一手文档背书) |

**`5` 的 TODO**(原则:不擅自映射/不擅自删除/不擅自标注):
- [ ] 找一手文档(通达信官方 / pytdx 源码注释 / 通达信客户端反编译)
- [ ] 在 pytdx 1.72 源码搜 `buyorsell` 上下文(本机已有 pytdx 源码)
- [ ] 跨多股多日验证 5 的出现规律(目前只验证 2 股 × 2 日 × 6 主机,样本太小)

---

## 5. 矩阵验收(2026-09-19)

`reports/local/tdx_matrix_validation_v1.json`(原数据 27 KB)

- **24 cell**:2 股票 × 2 日期 × 6 主机 = 24
- **结果**:24/24 全部成功,`pagination_complete=True` 全 True,`termination` 全 `last_page_short`

### 5.1 seen key 修复前后对比(代表性 1 cell)

`180.153.18.170 / 000001.SZ / 2026-08-27`:

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| raw_rows | 4614 | 4614 | 0(不变,服务端原始) |
| returned_rows | 4419 | **4432** | **+13** 救回 13 笔真成交 |
| rows_removed_by_seen | 195 | **182** | -13(被吞的真数据少 13 笔) |

**剩余 182 条** raw-rows gap 是**真重复**(服务端重复给或抓取重复),不是 wrapper bug,改 key 也救不回来。

### 5.2 24 cell 完整对比

| 主机 × 股票 × 日期 | v1 rows | v2 rows | v1 removed | v2 removed | 救回笔数 |
|---|---|---|---|---|---|
| 180.153.18.170 × 000001.SZ × 08-27 | 4419 | 4432 | 195 | 182 | +13 |
| 180.153.18.170 × 000001.SZ × 09-17 | 3911 | 3922 | 268 | 257 | +11 |
| 180.153.18.170 × 000006.SZ × 08-27 | 2276 | 2303 | 169 | 142 | +27 |
| 180.153.18.170 × 000006.SZ × 09-17 | 1698 | 1712 | 173 | 159 | +14 |
| 123.125.108.14 × 000001.SZ × 08-27 | 4401 | 4414 | 137 | 124 | +13 |
| ...(其他 18 cell 同模式) | | | | | |

---

## 6. 当前真实状态总结

| 范畴 | 状态 |
|---|---|
| **单元测试** | 18/18 全绿 |
| **矩阵验收** | 24/24 通过 |
| **生产 db** | 完全未动(0 行业务数据) |
| **`get_history_ticks` 旧接口** | 100% 未修(保留兼容) |
| **`fetch_history_ticks_with_meta` 新接口** | 100% 已修 |
| **`tbl_tick_v2` 表** | 不存在(需 `--execute` 触发) |
| **`update_ticks` 实际行为** | 走旧路径(因 v2 表不存在) |
| **buyorsell=5** | 文档化 TODO,代码原样透传 |
| **IP 选择 `random.choice`** | 未动(用户决定不属于数据正确性) |

---

## 7. 下一步可选路径

| 路径 | 含义 | 谁触发 |
|---|---|---|
| **P1. 跑迁移脚本** | `python3 scripts/migrate_tbl_tick_v1_to_v2.py --execute` | 用户手动 |
| **P2. 旧接口重定向** | `get_history_ticks` 内部改成调 `fetch_history_ticks_with_meta`(破坏性) | 用户决定 |
| **P3. 推进调用方迁移** | 让所有调用方改用新接口 + 新字段 `seqId_in_minute` | 各业务模块 PR |
| **P4. 改造 IP 选择** | `random.choice` → 优先 A/B 组(不动数据正确性,只改可靠性) | 用户决定 |
| **P5. 挖掘 buyorsell=5** | 在 pytdx 1.72 源码搜 + 跨更多股日验证 | 后续单独排期 |

---

## 8. 不要做的事(划线)

- ❌ 不擅自把 buyorsell=5 映射成"未知"或加 `buyorsell_note` 字段
- ❌ 不擅自把 buyorsell=5 / buyorsell=8 / vol=0 过滤掉(类似 B 组服务端行为)
- ❌ 不擅自把旧 `tbl_tick` 表 DROP 或 DELETE(只重命名 `tbl_tick_legacy`)
- ❌ 不擅自把 `get_history_ticks` 旧接口的 `seqId` 字段名重命名(会破坏现有调用方)

---

## 9. 评审/追溯线索

| 想看什么 | 看哪里 |
|---|---|
| 修复了什么代码 | `coreClient/tdx_ticks_meta.py` + `coreClient/tdx_client.py:443-451, 524-557` |
| schema 决策 | `docs/落库方案_v2.md` |
| 字段权威定义 | `coreClient/docs/tdx_client.md` § 9 |
| 矩阵验收原始数据 | `reports/local/tdx_matrix_validation_v1.json`(27 KB)|
| 矩阵验收备份(修复前) | `reports/local/tdx_matrix_validation_v1.before_seen_fix.json` |
| 18 个单元测试 | `coreClient/test/test_ticks_meta.py` |
| 迁移脚本 | `scripts/migrate_tbl_tick_v1_to_v2.py` |
| 数据集文档 | `docs/数据库路径说明.md`(旧) + `docs/落库方案_v2.md` § 3.1(新)|

---

文档作者:胖飞龟 / 2026-09-19 01:xx(北京时间)
变更必须追加到 `docs/修改记录/changelog_tick_当前状态.md`,不要批量补录。