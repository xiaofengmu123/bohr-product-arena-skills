# 审查清单与防误判守则（六维 + 证据规则 + 不编造红线）

本文件是执行 `result.json` 六维 `coverage` 与 findings 的事实依据。执行前必读；每条 finding 都必须能回到本清单的某一检查项，并引用 `references/severity-rubric.md` 定级。

## 0. 总体规则（先读）

- **报问题 ≠ 多报**：只看是否真实存在、能否回到原文某一句话、定位是否可核对、建议是否落到具体句子。宁少报、标 `pending_review`，不编造。
- **逐字引用**：`quote` 必须是从 `work/corpus.md` 中**逐字复制**的原文，不得改写、转述、省略关键限定词。定位符用 `p<页> ¶P<段落>`（无页码的文本来源省略 `p<页>`）。
- **区分 speaker**：每个主张标注 `author`（作者主张）/ `cited`（转述他人观点）/ `assumption`（明确假设）/ `control`（对照组/条件设置）。**转述他人观点、明确假设、有意设置的对照组不是缺陷**，除非作者把它们误当作自己的结论或事实。
- **每维必须落账**：`coverage` 六个维度逐一标 `done | partial | skipped`，`checked` 为实际核查条数；`partial/skipped` 必须给 `note` 或 `reason`（通常指向一条 `uncertainty` 或 `limitations` 的 id），不允许静默跳过。

## 1. 事实核验（factual_verification）

检查论文中**可被客观核验的事实性陈述**：数值、定义、单位、年代、人名、术语、机制表述是否与原文其他部分或常识/学科共识一致。

- 核对同一数值在摘要、正文、图表、讨论中是否一致（含单位、方向、百分比）。
- 区分"本文报告值"与"外部已知事实"：外部事实若无全文可核，标 `pending_review`，不得断言"错误"。
- 明显矛盾或不可能的数值 → `factual_error`；仅无法确认 → 记入 `uncertainty` 或 `pending_review`。

## 2. 引用支持（citation_support）

检查**文内引用是否真的支持所引的那句话**：引用目的（定义/结论/方法）、强度（结论 vs 数据 vs 背景）、是否张冠李戴。

- 无法获取被引文献全文时，标 `pending_review`，写"需外部核对"，**不得断言"引用不支持"**。
- 只有"引用格式/编号"问题不属于本维度（见维度 6）；本维度只看"内容是否支撑观点"。
- 引用他人观点却被表述为作者自己的结论 → 记入 `unsupported_citation` 或 `overclaim`，视强度定。

## 3. 方法设计（method_design）

检查**研究方法是否回答研究问题**、设计与问题是否对齐、样本/对象/对照是否充分、混杂与偏倚是否被处理。

- 对照组、安慰剂、随机化、盲法、预注册等**明确写出的设计**要如实识别，不得误判为缺陷。
- "方法回答不了研究问题" → `method_mismatch`；"缺关键设计细节"但可能只是没写 → 标 `uncertainty`，建议补方法细节。
- 替代解释未排除 → `argument_gap`（归论证链，见维度 5）。

## 4. 统计报告（statistical_reporting）

检查**统计口径与结论描述是否一致**：效应方向、显著性、置信区间、样本量、缺失数据处理、多重比较、p 值表述。

- 结论说"升高"而数据方向相反，或"不显著"却写成"有效" → `statistical_misreport` 或 `overclaim`。
- 只有相关性的设计却写因果结论 → `causal_overreach`（与维度 5 交叉时归入更贴切的一个）。
- 缺 p 值/置信区间/样本量等**报告不完整** → `advisory`（建议性意见，非事实错误）。

## 5. 论证链（argument_chain）

检查**从前提到结论的推理**：跳跃、隐含假设、替代解释、相关性因果混用、以偏概全。

- 前提是"相关性"、结论是"因果" → `causal_overreach`。
- 缺少中间步骤或隐含假设未说明 → `argument_gap`。
- 结论强度超出前提（"显著优于"却无对照数据）→ `overclaim`。

## 6. 学术规范（academic_norms）

检查**规范层面的可报告问题**：重复发表、数据可得性、伦理审批、利益冲突、作者贡献、未经声明地删改数据口径。

- **不包括**：文字润色、查重/学术不端判定、参考文献格式规范（题目明确排除）——这些不得作为 finding。
- 明显的数据可得性/伦理声明缺失 → `norm_issue`；证据不足时标 `pending_review`。

## 7. 跨章节一致性（单独做一遍）

引言承诺 → 方法/结果/讨论是否交付，逐条登记 `commitments[]`，`delivered_at` 给位置或 `null`。

- 承诺了要分析、结果没分析 → `commitment_unfulfilled`。
- 摘要结论 vs 正文结论、方法口径 vs 结果口径、图表 vs 正文描述不一致 → `inconsistency`。

## 8. 不编造红线（绝对禁止）

1. 不虚构 DOI、数值、引用、软件状态、文献内容；缺失就写 `pending_review` / `not_reported` / `null`。
2. `quote` 逐字来自 `work/corpus.md`；corpus 没有的句子不得出现。
3. 材料不足即 `status=insufficient_evidence`，不得用示例填充。
4. 长文被截断、章节缺失、无法外部核验 → 写 `limitations` / `uncertainty`，**不得宣称全文已查**。
5. 严重度、类型、状态与证据强度一致：证据弱就标 `probable` / `pending_review` / `low`，不得一律标"中"。
