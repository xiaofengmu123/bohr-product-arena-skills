# 评价量表（rubric）：维度、判据、分级与弃权规则

本表是 `litqa-audit` 的唯一判据来源，Agent 逐条落点、逐字取证时照此执行；`scripts/litqa.py grade` 的阈值与本表一致。任何一处修改都要同步更新本文件与 `scripts/litqa.py` 顶部常量。

## 1. 四类核心维度与检查点

每条评估记录字段：`dimension`（维度）、`checkpoint`（检查点）、`critical`（是否关键）、`verdict`（判定）、`judgment`（判断语句）、`locator`（证据位置）、`quote`（逐字引文）、`basis`（证据来源章节/图表）。

| 维度 | 定义 | 必查检查点（依文献类型增删） |
|---|---|---|
| `research_design` 研究设计 | 设计是否支持结论，类型识别是否正确 | 设计类型（RCT/队列/病例对照/横断面/系统综述/病例系列）、对照与比较组、干预/暴露定义、研究问题与设计匹配 |
| `evidence_sufficiency` 证据充分性 | 样本与数据是否足以支撑结论 | 样本量与抽样、结局与测量指标、统计口径（效应量/置信区间/P 值）、随访与缺失处理 |
| `bias_risk` 偏倚风险 | 偏倚落在哪些具体环节 | 随机化/分配隐藏/盲法、选择偏倚、信息/测量偏倚、混杂控制、失访与报告偏倚 |
| `reporting_completeness` 报告完整性 | 报告是否缺项、可否复核 | 方法与统计描述、结果数值与图表编号、注册与方案、局限性声明、数据可得性 |

文献类型特有检查点示例：RCT 加"分配隐藏/盲法/ITT"；观察性研究加"混杂与暴露-结局时序"；系统综述加"检索策略/PROSPERO 注册/偏倚评估工具"；预印本加"是否经同行评审、版本状态"。

## 2. 判定词表（受限取值，保证双人复核可比）

- `clear_risk`：原文明确出现缺陷/偏倚描述（如"未设盲"、"失访 30%"）。
- `possible_risk`：原文信息暗示风险，但未明说（如"未报告随机方法"但可能做了）。
- `no_risk_indicator`：原文提供了对应环节信息且未见风险迹象。
- `insufficient_evidence`：原文未报告该环节，无法判断，**弃权**。

弃权是合法结论：原文没写就写 `insufficient_evidence`，不得按"看起来合理"补缺。弃权条目仍要给出 `locator`（如方法章位置）与说明"未报告"。

## 3. 证据优先顺序与定位

1. 方法/结果原文 > 摘要 > 图/表 > 作者讨论（摘要易省略细节，讨论易过度解读）。
2. 每个关键结论必须落到句子级/表格级定位：`locator = "<源文件相对路径>#para:N"`，由 `scripts/litqa.py locators` 生成；`quote` 必须是该段落逐字片段。
3. 图表不可解析时，`locator` 标到图表编号所在段落，`quote` 保留原文表题，`limitations` 记"图表内容未抽取"。
4. 同一指标多源不一致时全部记录，并在 `uncertainty` 标 `source_conflict`，不隐藏、不择一。

## 4. 分级阈值（与 `scripts/litqa.py` 一致）

风险分：`clear_risk=3`、`possible_risk=2`、`insufficient_evidence=1`、`no_risk_indicator=0`。

- 维度内 `insufficient_evidence` 占比 ≥ 50% → 维度等级 `unassessable`（不可评价）。
- 否则任一 `critical` 检查点 `clear_risk` → 维度等级 `high_risk`（硬规则）。
- 否则按平均风险分：`< 0.75` → `low_risk`；`< 1.75` → `moderate_risk`；其余 → `high_risk`。

总体等级汇总：全部维度 `unassessable` → `unassessable`；任一维度 `high_risk` → `high_risk`；任一 `moderate_risk` → `moderate_risk`；否则 `low_risk`。等级必须给出 `rationale`（触发阈值的原因），不允许孤立标签。

## 5. 适用文献类型与限制

- 适用：RCT、观察性研究、系统综述/荟萃分析、诊断/预后研究、预印本、病例系列（各有特有检查点）。
- 不替代：同行评审、事实核验、临床/政策决策或研究结论本身。
- 限制：仅评输入材料中可得文本；全文不可得/仅摘要时，维度覆盖降级并如实标注，不补造数据。

## 6. 依赖与复用

- 仅依赖 Python 标准库；`python3`（或 `python`）≥ 3.8；PDF 抽取依赖平台 `pdftotext`，缺失时降级（见 SKILL.md §6）。
- 量表字段、判定词表、阈值常量可被他人直接引用；`assessment` 条目 JSON 是双人复核的对照模板。
