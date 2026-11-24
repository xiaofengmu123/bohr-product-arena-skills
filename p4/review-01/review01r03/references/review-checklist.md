# 六类审查锚点、严重度与误报抑制

本清单是 `check`/`build` 之外的“人工判据”层：脚本负责定位与逐字引用，Agent 负责依据下表逐 facet 判断。**quote 必须是原文逐字片段，判断必须落在 quote 所在位置。**

## 1. 六类 facet 检查锚点

| facet | 检查锚点（逐条排查） |
|---|---|
| `fact_check` | 数值/日期/名称/公式/单位/百分比/结论性陈述与原文前后是否一致；摘要与正文、正文与图表是否冲突；定义与术语是否前后统一 |
| `citation_support` | 正文论断与被引文献主张是否对应；被引文献是否真实存在、是否可定位；“支持”类引用是否真支持该论断；引用与论断错配的具体位置 |
| `method_design` | 对照设置、样本量与抽样方式、纳入/排除标准、评价指标选择、重复与随机化、偏倚控制 |
| `statistics_report` | 显著性报告、p 值/效应量/置信区间、误差、多重比较校正、统计方法是否与设计匹配、缺失值处理 |
| `argument_chain` | 从假设到结论的推理路径；缺失环节、循环论证、因果与相关混淆、结论外推超出数据范围、过度概括 |
| `academic_norms` | 数据可用性声明、伦理与利益冲突、图表版权与来源、作者贡献、目标期刊格式与术语要求 |

## 2. 严重度（severity）判据

| severity | 判据 | 示例 |
|---|---|---|
| `critical` | 结论直接建立在错误事实/缺失关键证据上，或触碰学术不端，足以推翻主要结论或阻断发表 | 关键数据与图表矛盾且结论依赖该数据；引用并不支持核心论断 |
| `major` | 明显削弱结论可信度或方法可复现性，需要实质修订 | 缺对照组、样本量未说明、显著性报告缺失 |
| `minor` | 局部错误/表述不准确，不影响整体结论，需小修 | 术语前后不一致、次要数值笔误 |
| `suggestion` | 可提升清晰度/完整性的改进意见，非缺陷 | 建议补充讨论、统一图表格式 |

## 3. 问题性质（issue_type）判据

| issue_type | 判据 |
|---|---|
| `factual_error` | 与原文内部证据/公认常识冲突，可判定为错误 |
| `insufficient_evidence` | 论断缺少支撑或无法核验，但不能断言为错 |
| `standards_issue` | 违反学术/出版/伦理规范 |
| `suggestion` | 改进性建议 |

**匹配原则**：`factual_error` → `confirmed`；`insufficient_evidence` → 常为 `confirmed`（证据确实缺失）或 `to_review`（可能是材料未覆盖）；`standards_issue` → 视确定性；`suggestion` → `to_review` 或 `confirmed`（作为建议）。

## 4. 误报抑制（必读，防止把正常现象判为缺陷）

- 学科写作惯例、期刊格式差异、作者有意取舍**不是**缺陷；只有违反已核实的规范或造成实质误导才记 `standards_issue`。
- 未找到参考文献全文时，`citation_support` 一律列 `unchecked_items`，写“无法核验”，**不得臆断被引文献主张**。
- 无法确认的条目标 `verdict=to_review` 或 `not_checked`，进入 `uncertainty`/`unchecked_items`，不得写 `confirmed`。
- 数值两处冲突时，两处都记录并标 `source_conflict`（进 `uncertainty`），不擅自取舍。
- 图片/图表读不出时，列缺口进 `pending_human`，不猜数值。
- 一条 finding 只对应一个可定位的问题；不要合并多个问题，也不要同一问题重复上报。

## 5. 确定性规则（本版强化）

1. finding 顺序以 `(facet 顺序, finding_id)` 排序，写入 `result.findings` 前由 `build` 统一排序，不依赖 Agent 手写顺序。
2. JSON 键序由代码显式决定（`review_schema.canonical_dumps`），不使用 `sort_keys=True` 以外的随机来源。
3. 易变量（时间戳、python 版本、输入目录、材料数）**只进 `runtime`**，`selftest.py` 比较前剥离 `runtime`。
4. `verdict_summary.counts.by_facet/by_severity/by_verdict` 预置全部枚举为 0，结构不随 finding 数量变化。
5. 同一输入重复运行，`case_id`/`sources`/`findings`/`counts`/`coverage` 必须一致；不满足即视为确定性失败。
