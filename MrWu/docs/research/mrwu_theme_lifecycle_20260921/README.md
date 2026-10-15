# A股题材生命周期与龙位更替研究 v0.2

本目录保存 2026-09-21 验收的题材生命周期描述性研究成果。

## 研究范围

- 区间：2025-10-30 至 2026-08-31，共 205 个交易日。
- 题材来源：开盘啦 KPL 涨停数据的原始 `lu_desc` 标签，不是同花顺题材。
- 核心对象：137 个活跃原始题材、793 段行情、80 个反复活跃题材、10 个单轮多日题材、678 只龙股。
- 本轮只做历史结构研究，不包含新闻归因、预测、买卖点、仓位、开盘30分钟执行规则或实盘建议。

## 建议阅读顺序

1. [PDF 报告](deliverables/A股题材生命周期与龙位更替研究_20251030_20260831_v0.2.pdf)
2. [研究方法与数据说明书](deliverables/RESEARCH_METHOD_AND_DATA_MANUAL_v0.2.md)
3. [机器可读说明书](deliverables/RESEARCH_METHOD_AND_DATA_MANUAL_v0.2.json)
4. [交付清单与哈希](deliverables/DELIVERY_MANIFEST_v0.2.json)
5. [Gemini 独立审核](deliverables/GEMINI_INDEPENDENT_AUDIT_v0.2.json)
6. `deliverables/data_tables_v0.2/` 中的完整伴随表

可编辑演示文稿：[PPTX](deliverables/A股题材生命周期与龙位更替研究_20251030_20260831_v0.2.pptx)。

## 两个新增专题

### 反复题材的跨段龙位更替

- 后续行情段共有 1,854 个非空段首前三槽位。
- 与紧邻上一段连续的槽位占 15.2%。
- 曾在任一更早段出现过的槽位占 32.2%。
- 新股票占 67.8%。
- 1,480 个“题材×股票”组合中，375 个至少跨两段进入前三，持续率为 25.3%。

### 单轮多日题材的段内换位

在全部 10 个单轮多日题材中，T+1：

- T 日龙1继续保持龙1：40.0%。
- T 日龙1仍在前三：60.0%。
- T 日龙2/3仍在前三：21.1%。
- 当日前三槽位中，新股票占 60.0%。

T+2 以后样本快速缩小，报告明确展示分母，不外推总体规律。

## 目录结构

```text
deliverables/
  A股题材生命周期与龙位更替研究_20251030_20260831_v0.2.pptx
  A股题材生命周期与龙位更替研究_20251030_20260831_v0.2.pdf
  RESEARCH_METHOD_AND_DATA_MANUAL_v0.2.md
  RESEARCH_METHOD_AND_DATA_MANUAL_v0.2.json
  GEMINI_INDEPENDENT_AUDIT_v0.2.json
  DELIVERY_MANIFEST_v0.2.json
  data_tables_v0.2/
reproduction/
  build_v02_extensions.py
  build_deck_v02.mjs
  MODIFICATION_FRAMEWORK_v0.2.md
```

## 复现说明

- `build_v02_extensions.py` 生成反复题材、单轮多日与 678 只龙股的 v0.2 扩展结果。
- `build_deck_v02.mjs` 通过 Artifact Tool 生成 27 页演示文稿。
- 两个脚本保留本轮本地绝对路径和运行时配置，迁移环境时需先调整路径。
- 原始数据库和大体量中间层不上传；输入文件哈希、行数、输出哈希及质量不变量记录在 `DATA_QUALITY_V02.json`。
- v0.2 输入运行前后哈希一致；同目录重跑输出逐文件哈希一致。

## 审核状态

- PPT：27 页、10 个原生图表、12 个原生表格；包结构与版面检查无发现项。
- PDF：27 页，逐页渲染检查完成。
- Gemini 3.1 Pro High 独立结构化审核结论：`PASS`。

