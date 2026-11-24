# 评价量表（rubric）：维度、检查点、判定词、分级阈值与弃权规则

本量表是 `assessment_matrix.json` 每一行的判据来源。目标：把"该研究设计合理、样本量适中"式的整体印象，拆成可逐条判断、逐条锚定证据、逐条可复核的条目。判断本身由 Agent 依据原文做出；本文件只规定维度、检查点、判定词表、证据优先级、分级阈值与弃权规则。

## 1. 四类核心维度（每条判断必须归属其一）

| 维度 id | 含义 | 回答的问题 |
|---|---|---|
| `research_design` | 研究设计 | 设计类型是否被正确识别？核心方法学要素是否充分且被报告？ |
| `evidence_sufficiency` | 证据充分性 | 样本/信息量、效应大小与精度、结局定义与随访是否足以支撑结论？ |
| `bias_risk` | 偏倚风险 | 偏倚落在哪些具体环节，程度如何（明确存在/可能存在/未见迹象/证据不足）？ |
| `reporting_completeness` | 报告完整性 | 方法、结果、局限、利益冲突、数据可得性、预注册是否完整披露？ |

## 2. 判定词表（受控词，与脚本 `SEVERITY`/`UNASSESSABLE` 一致）

- 质量/充分性：`adequate`（充分）、`partial`（部分）、`inadequate`（不充分）。
- 风险：`low_risk`、`moderate_risk`、`high_risk`、`critical_risk`。
- 偏倚表现（对应题目要求）：`definite`（明确存在）、`possible`（可能存在）、`absent_sign`（未见迹象）、`unclear`（不清）。
- 不可评价：`not_reported`（原文未报告）、`insufficient_evidence`（可得材料不足以判断，如仅摘要）、`not_applicable`（不适用于该研究类型）。
- 报告类：`reported`（已报告）、`present`（存在）、`absent`（缺失）。

一条判断必须且只能取一个受控词；`not_reported` 与 `insufficient_evidence` 的区别必须写清：前者是"论文没写"，后者是"材料拿不到全文/图表无法解析，无法判断"。

## 3. 文献类型特有检查点（选择与文献类型匹配的集合）

- **RCT**：随机序列生成、分配隐藏、盲法（受试者/干预者/评估者）、意向性分析（ITT）、把握度/样本量计算、失访与依从、结局预先指定、选择性报告。
- **观察性研究（队列/病例对照/横断面）**：暴露与结局定义与测量、对照组选择、时间先后、混杂控制与调整、失访、多重比较。
- **系统综述/Meta 分析**：方案/预注册、检索策略与数据库、双人独立筛选与提取、纳入研究的偏倚风险评估、异质性（I²）与合并模型、发表偏倚（漏斗图/检验）、证据确定性（GRADE）。
- **诊断准确性**：参考标准、盲法与独立判读、病例谱系（避免谱系偏倚）、验证偏倚、阈值选择。
- **临床前/实验室**：随机化与盲法、样本量/功效、重复实验、阳性/阴性对照、盲态结局评估。
- **质性研究**：抽样策略、数据饱和、反身性、三角验证、编码过程。
- **调查**：抽样框、应答率、无应答偏倚、测量工具信效度。

## 4. 证据优先级（`basis` 字段）

1. `direct_quote`：方法与结果部分的逐字原文（最优先，最可复核）。
2. `table`：表格/图中数据（标注图表编号）。
3. `section`：段落级陈述。
4. `inference`：由已报告事实的合理推断（必须写明推断链条）。
5. `missing`：无依据 → 必须弃权（`abstained=true`），不得编造。

每条判断的 `quote` 必须是 `locator` 所指锚点的**逐字子串**（空白折叠后仍相等），否则 `verify` 会标记未命中。

## 5. 分级阈值（与 `scripts/litqa.py grade` 一致，显式可查）

判定词 → 严重度：`{low_risk/adequate/absent_sign/reported/absent/no_sign: 0, moderate_risk/partial/possible/unclear/present: 1, high_risk/inadequate/definite: 2, critical_risk: 3}`；不可评价词 → `None`（不计入严重度，计入 `unassessable` 计数）。

- **维度风险**：`risk = severity_to_risk(max_severity)`；若该维度的关键检查点（`critical=true`）严重度 ≥2，则维度风险至少升到 `high`。
- **总体风险**：所有可评价维度的最严等级（`overall = max(per-dimension risk)`）；全部不可评价 → `insufficient_evidence`。
- 每条 `grade` 记录 `derivation` 文本，说明"哪个维度贡献了总体等级"，避免孤立标签。

## 6. 弃权与缺口规则

- 仅摘要/全文不可得：所有依赖方法与结果的检查点判 `insufficient_evidence`，`abstained=true`，`limitations` 写"仅摘要，无法核验方法与结果"。
- 图表不可解析：相关数据条目判 `insufficient_evidence`，在 `degradations` 记录"图表未解析"。
- 语言或预印本特殊：如实标注 `preprint`/`language`，结论强度相应下调，不假装已同行评审。
- 关键证据缺失：对应条目判 `not_reported` 或 `insufficient_evidence`，写入 `pending_human` 而非补齐。
- 冲突信息：同一指标多来源不一致，全部记录并标 `source_conflict`，该条判 `unclear`。

## 7. 适用与限制

本量表聚焦研究设计与报告质量，不替代同行评审、事实核验、临床/政策决策或研究结论；不要求覆盖全部研究设计与全部学科，只要求在一个明确文献类型与维度集合上做到可复核。
