# 资料与代码来源

执行范围以用户2026-10-05接续指令及《Astra总控接续执行书》v1.0为准。本次保留旧v0.4作为档案，以Claude五模块参考脚本为B0基线，再建立F1正式版与C0探索性相位。

## 背景资料（已读取相关原文）

- [项目回顾与阶段检讨v2](https://drive.google.com/file/d/1Y39Yq6XReEkBxQdPOewbSeJurwYChEa4/view)：研究顺序、已有工程资产及环境层缺口。本仓库有[同名文档](../../../policyStudy/docs/project_rocket_review_v2_20261004.md)。
- [李老师反思要点与评估](https://drive.google.com/file/d/1hNJDvrrggvSdKbgX2Coz4fzRrdHOI6rC/view)：完整分母、失败样本、样本已被研究的问题；本报告没有把其中收益数字用作本次实测证据。
- [原始复盘与早期讨论](https://docs.google.com/document/d/1w_Vm3Tqn8SIgIeFEQFDYPi9B5vkQ9TE6_YDGnh2Nk-M/edit)：用于human_source.csv中的必要短摘录。原文包含当日表述、周度回顾及次日回顾，分别标记。首次发布时间与历史不可篡改性未经本次认证。
- [接力情绪状态机设计说明](https://drive.google.com/file/d/1-kTUtj8bMRwnNpzWwyH6Nlu0OY2yUdI5/view)：定义、模块及10日外部对账转述。与本轮执行书冲突处以新授权为准。
- [通用委派方法与复盘](https://drive.google.com/file/d/1fntwTQdSPPiNKGqug8JPDcyJp_aToRQf/view)：有界任务、检查点和独立验收；不作为模型能力基准。

## 算法与软件

- 五个原始参考脚本与415日CSV在 [baseline_reference](../../../policyStudy/policy/speculation_sentiment_lite/baseline_reference/)。B0入口只适配输入路径并执行原算法；归档脚本里的旧绝对路径不是正式复刻入口。
- 题材沿用现有theme-market-review-v2及其DuckDB服务。MrWu原缺少这些依赖，必要文件按哈希登记在 [REUSED_CODE.json](../../../policyStudy/policy/speculation_sentiment_lite/config/REUSED_CODE.json)。唯一查询性能改动是跳过起始日之前的输出组装，保留全部历史用于排名和百分位，172日完整JSON与原接口逐项相同。
- PDF使用ReportLab与Matplotlib；字体为 [Google Fonts Noto Sans SC](https://github.com/google/fonts/tree/main/ofl/notosanssc) 的静态子集RocketSansSC。保留 [SIL OFL 1.1许可](reproduction/fonts/OFL.txt)，不需要Microsoft Office或Codex专用运行库。

完整私人聊天、账户故事、原始数据库与凭据不在发布包中。报告的研究数字由附表直接生成，云端叙述仅作注明对象和时间属性的对照。
