# 论文质量审查：检查清单与判定依据

本文件是六类分面的检查锚点、严重度/问题性质/判定级别的判据，以及误报抑制规则。Agent 逐条审查时以本文件为统一口径，`main.py check` 只做机械校验，判据靠本文件。

## 1. finding 字段定义

| 字段 | 必填 | 说明 |
|---|---|---|
| `finding_id` | 是 | 顺序编号，如 `F01` |
| `facet` | 是 | 六类之一（见下） |
| `severity` | 是 | `critical/major/minor/suggestion` |
| `issue_type` | 是 | `factual_error/insufficient_evidence/standards_issue/suggestion` |
| `verdict` | 是 | `confirmed/to_review/not_checked` |
| `claim` | 是 | 本文论断或问题描述，一句话 |
| `location` | 是 | 段落定位：`para7` 或 `p3#para2` |
| `quote` | 是 | 原文逐字片段，可跨行不跨段 |
| `rationale` | 是 | 判断依据，说明为何如此判定 |
| `revision_suggestion` | 是 | 可执行的修改建议 |
| `confidence` | 是 | `high/medium/low` |
| `evidence` | 是 | 证据数组，每项含 `source_id/locator/quote` |

## 2. 六类分面检查锚点

### fact_check（事实核验）
- 摘要、正文、图表、表格中的数值是否一致；单位、小数位、百分号是否一致。
- 关键事实（病种、人群、方法名称、定义、结论）是否前后统一。
- 与外部公认事实冲突处需谨慎：只报“稿内不一致”或“有据可查的硬错误”，不凭记忆断言。

### citation_support（引用支持）
- 正文论断与被引文献主张是否对应：列出“本文论断 vs 被引文献主张”。
- 数据类引用是否来自对应来源；二次引用是否标注。
- 参考文献列表是否缺失、编号是否与正文对应。

### method_design（方法设计）
- 对照设置、样本量与抽样方式、评价指标选择、重复与随机化说明。
- 区分“方法本身有缺陷”与“方法可接受但报告不全”，前者标 `factual_error`，后者标 `standards_issue`。

### statistics_report（统计报告）
- 显著性报告（p 值、检验名、方向性）、误差与置信区间、效应量。
- 是否报告了统计方法、显著性水平、多重比较校正。

### argument_chain（论证链）
- 从假设到结论的推理路径：缺失环节、循环论证、因果与相关混淆、结论外推超出数据范围。

### academic_norms（学术规范）
- 数据可用性声明、伦理与利益冲突声明、图表版权与来源、术语与格式是否符合目标期刊。
- 把规范问题与实质问题分开呈现。

## 3. 严重度判据

| severity | 适用 |
|---|---|
| `critical` | 事实硬错误、引用与论断明显错配、方法致命缺陷、结论外推导致结论不可信 |
| `major` | 关键证据缺失、统计量缺失、对照缺失、论证断裂，影响结论可靠性 |
| `minor` | 局部不一致、术语不统一、规范缺失，不影响主结论 |
| `suggestion` | 可读性、可改进但非缺陷的意见 |

## 4. 问题性质判据

| issue_type | 适用 |
|---|---|
| `factual_error` | 稿内自相矛盾或与权威来源冲突的硬错误 |
| `insufficient_evidence` | 论断缺少支撑、材料不足、无法核验 |
| `standards_issue` | 报告不全、格式/规范不合要求，但方法本身可接受 |
| `suggestion` | 建议性意见 |

## 5. 判定级别判据

| verdict | 适用 |
|---|---|
| `confirmed` | 原文可定位、判据明确，可下结论 |
| `to_review` | 存在疑点但证据不足，或需作者澄清，不擅自定性 |
| `not_checked` | 因缺材料未检查 |

## 6. 误报抑制规则

1. 学科写作惯例、期刊格式差异、作者有意取舍不判为缺陷，除非与题面要求冲突。
2. 不凭 Agent 记忆断言外部事实；外部事实需有可追溯来源并写入 `evidence`。
3. 同一问题不重复拆成多条；合并到同一条 finding，用 `rationale` 说明范围。
4. 只报“稿内不一致”或“有据可查的错误”，不报“我不确定”。
5. 数值冲突时不擅自取舍：两处都记录，标 `source_conflict` 放入 `uncertainty`。

## 7. 证据溯源规则

- 每条关键结论/数值都要能指回材料的 `locator + 逐字 quote`。
- `quote` 必须逐字命中原文（`check` 会校验）；跨行不跨段。
- 冲突信息不隐藏：同一指标两来源不一致时，两个都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
