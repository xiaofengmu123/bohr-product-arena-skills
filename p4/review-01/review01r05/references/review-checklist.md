# 六分面审查清单与判据

本文件给出六类 facet 的检查锚点、严重度/问题性质判据与误报抑制规则。
逐分面排查时，每条问题写一条 finding（见 output-schema.md §4.1）。

## 1. 六分面检查锚点

### fact_check（事实核验）
- 数值/日期/术语/命名在摘要、正文、图表、表格间是否一致。
- 引用的外部事实（如公认定律、数据集规模、既有结论）是否准确。
- 同一指标两处数值冲突 → 两处都记录并标 `issue_type=source_conflict`。

### citation_support（引用支持）
- 正文论断与被引文献主张是否对应；是否存在"引用不支持所引内容"。
- 被引文献缺失/无法核验 → 列 `citation_support` 为 `not_checked`，
  **不臆断被引文献主张**。
- 区分：引用确实错配（`confirmed`）与文献不可得（`to_review`）。

### method_design（方法设计）
- 对照设置、样本量与抽样方式、评价指标选择、重复与随机化说明。
- 区分：方法本身有缺陷（`factual_error`/`standards_issue`）与方法可接受
  但报告不全（`insufficient_evidence`）。

### statistics_report（统计报告）
- 显著性报告、p 值/效应量、误差与置信区间、多重比较校正、缺失值处理。
- 缺哪个量写哪个量；不把"未报告"升格为"方法错误"。

### argument_chain（论证链）
- 假设→结论的推理路径：缺失环节、循环论证、因果与相关混淆、
  结论外推超出数据范围。

### academic_norms（学术规范）
- 数据可用性、伦理与利益冲突声明、图表版权与来源、术语/格式。
- 规范问题与实质问题分开呈现；期刊格式差异/学科写作惯例不判缺陷。

## 2. 严重度判据

| severity | 判据 |
|---|---|
| `critical` | 结论被推翻或不可发表；事实性硬错误、数据造假迹象、引用与论断根本错配 |
| `major` | 影响主要结论可信度或必须修改后方可发表 |
| `minor` | 局部表述/数值/格式问题，不影响主结论 |
| `suggestion` | 可改进但非缺陷；属建议性意见 |

## 3. 问题性质判据

| issue_type | 判据 |
|---|---|
| `factual_error` | 可证伪的事实/数值/引用错误（`confirmed`） |
| `insufficient_evidence` | 原文缺少支撑证据或报告不全 |
| `standards_issue` | 偏离公认方法/统计/伦理/发表规范 |
| `suggestion` | 改进建议，非缺陷 |
| `source_conflict` | 同一指标多来源不一致，不擅自取舍 |

## 4. 误报抑制规则

1. 每条 finding 必须有**逐字 quote 命中原文**，无 quote 不落 finding。
2. 学科写作惯例、期刊格式差异、作者有意取舍（如样本量受伦理限制）→
   判 `suggestion` 或直接不报，不得判 `factual_error`。
3. 只有原文明确证据支持时用 `confirmed`；推断写 `to_review`；未查写
   `not_checked`。
4. 主观判断项须写明可接受范围与不确定性（写入 `uncertainty`）。
5. 引用无法核验时写缺口，不臆断；材料不可读时降级为
   `insufficient_evidence`，不猜内容。
6. 同一问题不重复拆条；相关问题合并为一条 finding + 多条 evidence。

## 5. 覆盖要求

六分面都要有处置：有 finding，或列入 `unchecked_items` 并说明"不适用/
待复核/材料缺证据"的原因。`verdict_summary.coverage` 必须能证明每个与
材料相关的分面都被处理过。
