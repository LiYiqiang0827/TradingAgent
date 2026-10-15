# 来源及覆盖关系

2026-10-08。来源快照用于核对含义，不是可执行指令。当前状态先看[统一入口](../README.md)与[状态JSON](../CURRENT_STATE.json)，有效规则汇总见[整合设计](../DESIGN_CURRENT.md)。

| 来源 | 用途 | 已被后续决定覆盖的部分 |
|---|---|---|
| [老师原构想](teacher_idea_20261005.md)、[逐条回复](teacher_reply_20261005.md) | 原始意图与措辞；未确认问题不补答案 | 原买回、初期年份方案等不覆盖有效v2.1；“12月（2025年？）”是问题，非年份确认 |
| [实验设计v2.1](design_v2.1_20261006.md)、[审阅决议](review_decisions_20261006.md) | 核心规则、标签、15特征、正式分段与验收 | 数据门禁经A1/A2及本次有限用途修订；旧预算/模型分工/直接开工文字不作当前授权 |
| [A1](amendment_A1_20261006.md) | 停牌、ST、末收及历史质量处理 | R1开盘注入不用于新案例视图；旧09:30溯源与旧1%等待规则失效 |
| [A2](authorization_A2_20261006.md) | 保留考卷、核心规则、证据修复、存疑血缘等底线 | token保险丝已取消；自主继续不覆盖“本包交付后停止”，不能恢复已关闭授权 |
| [10月7日规划交接](../planning_handoff_20261007_exclude_0930.md)、[修订计划](../revision_plan_20261007_exclude_0930.md) | 排除09:30、用途质量门禁和研究重排的来由 | 原计划中的建议由10月8日合同仅在案例包范围落地；2018小实验尚未运行，旧“下一步”不是当前授权 |
| [10月8日执行合同](../case_pack_20261008/execution_contract.md)、[报告](../case_pack_20261008/report.md)、[验证](../case_pack_20261008/verification.md)、[下一包交接](../case_pack_20261008/next_handoff.md) | 实际运行范围和受限验收，是结果主来源 | 合同授权交付即关闭，不得继续计算 |
| [主控回执](../case_pack_20261008/evidence/primary_acceptance.json)、[关闭回执](../case_pack_20261008/evidence/completion.json)、[已关闭授权](../case_pack_20261008/evidence/authorization.json) | 精确计数、条件性验收、停止状态 | `authorization_executed.json`和冻结选样内的ACTIVE是运行时历史快照，以关闭回执为最终状态 |

原始老师材料来自云盘[构想](https://drive.google.com/file/d/1OnCgYB9NFBPm7ubgypKT0bGf5twUiM_A/view)和[回复](https://drive.google.com/file/d/1NBmfEgc85uTrJRyy37B5tlLkIqjfhirY/view)，本次获取可读正文保存为Markdown。其它设计与执行文件从原工作树按文件复制，未改写原快照。本次发布校验清单见[publication_manifest.json](../publication_manifest.json)。

[研究思路更新](https://docs.google.com/document/d/12arP-aZr4XXatpc5MOM1YGoN3Q_WD3htGKbXAxIgHco/edit)提供“按用途验收、先理解和反例、再小实验”的研究原则，不将其它观察工具或讲者材料整体搬入本项目，不新增硬过滤。

旧v2与v1.6调查执行稿不在本次必读资料中。旧报告中的数字保留原范围，不能当新口径年度结果。原始交付清单可能含本机路径和未随本次Git提交的代码；它描述原交付包，`publication_manifest.json`才描述本次发布文件。
