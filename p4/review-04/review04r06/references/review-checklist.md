# 六类检查清单与防误判守则

本文档是 Agent 逐条审查时的**判定规则**，不是模板答案。所有判断必须回到
`work/corpus.md` 中某个段落 ID（`p<页> ¶P<段落>`）并给出逐字原文；找不到原句
或拿不准时，标 `pending_review` 或 `uncertainty`，不得编造。

## 0. 总体守则

1. 每条 finding 必须同时给出：`locator`（段落级）+ 逐字 `quote` + 被核查 `claim`
   + `evidence`（判断依据）+ `reasoning`（三问）+ 落到具体句子的 `suggestion`。
2. 每个结论区分说话人：`author`（作者主张）/ `cited`（转述他人观点）/
   `assumption`（明确标注的假设情形）。三者不可混为一谈。
3. 宁可少报、标 `pending_review`，不可编造。冲突信息不隐藏：同一指标两个来源
   不一致时，两条都记并标 `source_conflict`（写入 `uncertainty`）。
4. 只审"论点—证据—推理链—章节衔接"的逻辑问题；不审文字润色、查重、参考文献
   格式、原始数据重算、投稿策略（题目明确排除）。

## 1. 事实核验（factual_verification）

- 数值、定义、机理、样本量、效应方向是否与本文其余部分或所引来源一致？
- 摘要与正文、正文与图表描述之间的数值是否打架？
- 常见类型：`overclaim`、`statistical_misreport`、`consistency_break`。
- 无外部原文可核对时：只判"本文内部是否自洽"，外部真伪标 `pending_review`。

## 2. 引用支持（citation_support）

- 引文是否支持被引的那句话（而非只支持大意）？
- 是否把"转述他人观点"当成"作者结论"报为缺陷（禁止）？
- 无法获取引文全文时：该条标 `pending_review`，写"需外部核对"，不得断言
  "引用不支持"。
- 常见类型：`unsupported_citation`。

## 3. 方法设计（method_design）

- 方法能否回答引言提出的研究问题？
- 测量/干预/对照组设计是否与结论口径匹配？
- 常见类型：`method_mismatch`、`reasoning_gap`。
- 对照组、控制变量、敏感性分析是有意设计，不是缺陷（见第 7 节）。

## 4. 统计报告（statistical_reporting）

- 统计口径（效应方向、显著性、置信区间、样本量、p 值）与结论表述方向是否一致？
- 是否把"无显著差异"说成"证明无差异"、把"相关"说成"因果"？
- 常见类型：`statistical_misreport`、`causality_conflation`。
- 只判"报告口径是否自洽"，不做原始数据重算（题目排除）。

## 5. 论证链（argument_chain）

- 前提→结论是否存在跳跃？隐含假设是否未说明？替代解释是否未排除？
- 结论强度是否超出数据/设计能支撑的范围？
- 常见类型：`overclaim`、`reasoning_gap`、`causality_conflation`。

## 6. 学术规范（academic_norms）

- 数据可用性声明、伦理/利益冲突披露、图表与文字对应等是否缺失或矛盾。
- 只报"影响可核验性或完整性的规范问题"，不做学术不端判定（题目排除）。
- 常见类型：`norm_issue`、`suggestion`。

## 7. 防误判守则（误判 = 报出不成立的缺陷，直接扣正确性）

以下情形**不得**报为缺陷：
- 有意设置的**对照组 / 控制变量 / 安慰剂组**；
- 明确标注的**假设情形 / 局限性 / 未来工作**；
- **转述他人观点**（标注 `cited`），即便该观点与作者结论相左；
- 论文自述的**范围限制**（如样本量小的声明）。

遇到疑似冲突，先问三句再落笔：这是作者主张还是转述？是结论还是假设？是本文
数据还是引文数据？

## 8. 主张抽取规范

- 优先从摘要、引言、结果、讨论四类章节抽取"可判定真伪或强度"的主张。
- 每条 claim 登记：`speaker` / `section` / `locator` / `verdict`
  （`supported | overclaimed | unsupported | pending`）/ `evidence`。
- 引言中的承诺（"本文将分析 X"）单独登记为 `commitments[]`，并在第 4 步做
  "承诺—交付"对照：`delivered_at` 填结果/讨论位置，未交付填 `null` 且
  `status=missing`。
