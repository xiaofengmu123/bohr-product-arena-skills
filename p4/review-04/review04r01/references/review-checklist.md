# 六维审查清单与防误判守则

本清单用于逐段核查科研论文/材料。**核心目标：每条结论必须真实存在、能回到原文某一句话、建议能落到具体句子。** 宁可少报、标 `pending_review`，不可编造。

## 0. 主张抽取（先做，登记 claims / commitments）

从摘要、引言、结果、讨论四部分抽取作者核心主张及其限定条件，逐条登记 `claims[]`：

- 每条主张写 `speaker`：`author`（作者主张）、`cited`（转述他人观点）、`assumption`（明确标注的假设情形）、`mixed`（混合）。
- 引言中的承诺登记为 `commitments[]`：`promise`（逐字承诺原文）、`locator`、`delivered_at`（结果/讨论中交付的位置或 `null`）。
- 判断 `verdict`：`supported / overclaimed / unsupported / pending`。

**防误判铁律**：以下不是缺陷，报出即为误报——
- 有意识置的对照组（control group）；
- 明确标注为假设、示例、估算、模拟的情形；
- 转述他人观点（cited 内容不代表作者立场）；
- 原文明确写出的研究局限（limitation 不是新发现的问题）。

## 1. 事实核验（factual_verification）

- 数值、单位、年份、名称、缩写展开是否自洽；
- 摘要数值与正文数值是否一致；图表数值与正文描述是否一致；
- 无法外部核验的数值：标 `pending_review`，写“需外部核对”，**不得断言错误**；
- 可核验的错误写 `factual_error`，证据写“原文 X 处写 A，而 Y 处写 B”，给出两处 locator。

## 2. 引用支持（citation_support）

- 引文是否支持所引的那句话（需区分“文献确实证明了 X”与“文献只是提到 X”）；
- 无法获取被引全文时：标 `pending_review`，写“需外部核对该引用”，**不得断言“引用不支持”**；
- 引用支持不足写 `unsupported_citation`，证据写所引观点的原文与作者如何使用它。

## 3. 方法设计（method_design）

- 研究方法能否回答研究问题（对象、处理、对照、样本、测量是否匹配问题）；
- 样本量与设计是否支撑结论的泛化范围；
- 方法回答不了问题时写 `method_mismatch`，指出“问题问 X，方法只能回答 Y”。

## 4. 统计报告（statistical_report）

- 是否报告样本量、效应量、置信区间/显著性水平；口径前后是否一致；
- 统计方向与结论描述方向是否相反（例：负相关被写成正相关）；
- 相关性与因果性是否混用（观察性/相关性设计却用因果表述）→ `causality_conflation`；
- 统计口径与结论不一致 → `statistical_error`；未做重算，只做“报告与结论是否自洽”的检查。

## 5. 论证链（argument_chain）

- 从前提（数据/文献）到结论是否存在跳跃、隐含假设未说明、替代解释未排除；
- 结论强度是否超出证据支持范围 → `overclaim`；
- 缺少必要证据 → `missing_evidence`；
- 论证断裂/隐含假设 → `argument_gap`。

## 6. 学术规范（academic_norm）

- 只查影响可信度的规范问题（数据可得性、利益冲突、伦理声明缺失等）；
- **不负责**：文字润色、查重/学术不端判定、参考文献格式检查（题目明确排除）；
- 属规范问题写 `norm_violation`，属可改进项写 `suggestion`（明确标注“建议，非缺陷”）。

## 7. 跨章节一致性（单独做一遍）

- 引言承诺 → 方法/结果是否交付（`commitments[]` 逐条对）；
- 方法口径 → 结果口径是否一致；摘要 → 正文结论是否一致；图表 → 正文描述是否对应；
- 断裂写 `cross_section_gap` 或 `consistency_issue`，给出承诺处与交付处两处 locator。

## 8. 证据溯源工作流（本 Skill 的强化点）

每条 finding 必须带 `evidence_refs[]` 指向 `evidence_map.items[]`；每条 evidence item 至少含：

- `source`（材料路径）、`locator`（p页 ¶P段落）、`quote`（逐字原文）；
- `kind`（direct_quote / paraphrase / data_point / citation / method_statement / statistical_report / missing_evidence / cross_section_commitment / figure_table）；
- `status`（verified / needs_external_check / incomplete / conflict）；
- `supports[]` / `contradicts[]`（指向 finding/claim id）。

同一指标两个来源不一致时：**两个都记录**，在 `evidence_map.conflicts[]` 中写 `source_a` 与 `source_b`（各含 source+locator+quote），标 `source_conflict`，不隐藏冲突。

`quote` 必须与语料库逐字一致，禁止改写；`locator` 必须能在 `work/corpus.md` 或材料文件中定位到那句话。
