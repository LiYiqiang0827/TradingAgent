# 15分钟均线粘合：两组约束

2026年9月30日用户确认：两组使用不同阈值，并在同一根已收完的15分钟K线上同时达标。

| 组 | 参与数值 | 上限 | 作用 |
|---|---|---:|---|
| 第一组 | 收盘价、MA5、MA10、MA20、MA30 | 1.0% | 短期价格和均线已经紧密聚拢 |
| 第二组 | 第一组所有数值，再加入MA60 | 2.0% | MA60也已经接近这个聚拢区域 |

两组均采用 `100 × (最大值 / 最小值 − 1)`。第二组保留MA30，不能用MA60替换MA30；不能取两组较小值，也不能跨两根K线分别满足条件。收盘价必须参与，缺少任意一条所需均线时不通过。统一复权口径只改变共同价格尺度，不改变该比值。

第二组的宽度必然不小于第一组，因此采用更宽的2.0%上限。程序分别保留 `coil_short_ok`、`coil_with_ma60_ok`、两组宽度及最终AND结果，便于辨别失败原因。

MA120、MA250不加入粘合宽度，继续用于原长期位置和持续上升约束。原起涨来源、慢回调、历史多头、前周压缩、粘合后1—2根放量阳线及触发时六线严格多头/上升等其他条件保持原数值。再启动价格继续要求突破第一组上沿0.1%，第二组用于MA60贴近程度的约束；没有另加“必须突破第二组上沿”的条件。

## 代码与参数

- `policyStudy/policy/题材涨停研究/scripts/study_fifteen_min_ma_coil.py` 的 `Rules()` 默认是新标准：`coil_mode="dual"`、`coil_pct=1.0`、`coil_with_ma60_pct=2.0`。
- 固定配置：`policyStudy/policy/题材涨停研究/config/ma_coil_dual_group_1pct_2pct.json`。
- `coil_features()` 独立计算两组；`features()` 和 `find_signals()` 使用同一约束，信号记录同时保存两组宽度和两个阈值。
- 新回放默认输出至独立的 `outputs/fifteen_min_ma_coil_20260901_20260915_v3_dual`，冻结完整规则为 `rules.json`。不得覆盖原v2结果。
- 旧规则通过 `load_rules()` 读取；没有 `coil_mode` 的旧JSON恢复 `legacy_or`，保留当时的MA30/MA60替代分支。这用于复现旧研究，不是新默认规则。
- 评分模式验证冻结规则、日期及信号哈希；不能把新参数套在旧信号上当作独立回放。

```bash
PYTHONPATH=.:offlineDataManager/scripts .venv/bin/python \
  policyStudy/policy/题材涨停研究/scripts/replay_fifteen_min_ma_coil.py \
  --first 20260901 --last 20260915 --warmup-start 20260701 \
  --rules-file policyStudy/policy/题材涨停研究/config/ma_coil_dual_group_1pct_2pct.json \
  --output outputs/fifteen_min_ma_coil_20260901_20260915_v3_dual
```

命令供后续正式回放使用。本次调整不据此宣称已经完成全市场新回放或验证盈利。此前410次及20张示例按旧规则选出，仍需重新扫描才能得到新规则的完整信号分母。

## 验证

```bash
PYTHONPATH=.:offlineDataManager/scripts:policyStudy/policy/题材涨停研究/scripts \
  .venv/bin/python -m unittest discover \
  -s policyStudy/policy/题材涨停研究/scripts -p test_study_fifteen_min_ma_coil.py -v
```

回归覆盖：第二组确实包含MA30、两个阈值分别生效、同根同时通过、收盘价参与、MA60缺失不通过、复权尺度不影响结果、旧规则兼容、信号输出及未来信息隔离。

本次30项回归全部通过。用原随机20例的股票及7只模板票，共27只股票的冻结输入作实现检查：旧规则29个信号的时刻、分支及宽度完全复现；新规则形成19个技术信号，逐个截去未来重新计算，均保持一致。新诊断未做收益评分，也未对其重新执行ST/上市年龄门禁，不能作为新交易策略分母。

7只模板票保留之前的测量时刻，不再挑最小分支；在这些固定时刻，桂林旅游、闽东电力、瑞尔特两组同时通过。万向德农、新赛股份第二组超限，龙版传媒、百大集团第一组超限。这只是已知模板的固定时点测量，不是预测命中率。

实现验收与两个明细表保存在 `outputs/fifteen_min_ma_coil_20260901_20260915_v2/dual_group_validation/`。`ACCEPTANCE.json`记录源代码哈希、规则哈希及旧冻结文件未被改动的核对结果。
