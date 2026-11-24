# 评价量表与分级阈值（litqa-evidence）

本量表驱动全部评价：评价者按条目逐条判定，每条绑定 `locator` + 逐字 `quote`；`scripts/litqa.py grade` 按固定阈值聚合风险等级。阈值、判定词、证据优先顺序与本文件严格一致，任何修改必须同步更新脚本中的 `THRESHOLDS`。

## 1. 判定词（受控词表）

### 1.1 条目判定 verdict（研究设计 / 证据充分性 / 报告完整性）

| verdict | 含义 | 适用情形 |
|---|---|---|
| `supported` | 支持 | 原文有逐字证据且与判据一致 |
| `partial` | 部分支持 | 证据不完整或部分一致 |
| `contradicted` | 不支持 | 原文证据与判据冲突 |
| `not_reported` | 未报告 | 该检查点对本类型应报告但原文未提 |
| `insufficient_evidence` | 证据不足 | 全文不可得/仅摘要/图表不可解析，无法判定 |
| `not_applicable` | 不适用 | 该文献类型/设计不要求此检查点 |

### 1.2 偏倚判定 verdict（仅偏倚风险维度）

| verdict | 含义 |
|---|---|
| `present` | 明确存在：原文描述了该偏倚的具体表现 |
| `possible` | 可能存在：有线索但未明确 |
| `absent` | 未见迹象：该领域已报告且无该偏倚迹象 |
| `insufficient` | 证据不足：原文未报告该领域信息，无法判定 |

### 1.3 总体风险等级 risk_level

`low`（低）→ `some`（中）→ `high`（高）→ `unclear`（不可评价）。

## 2. 四类维度与检查点

### 2.1 研究设计（research_design）

- D1 研究设计类型是否明确（RCT/队列/病例对照/横断面/定性/系统综述等），且与原文表述一致。**[critical]**
- D2 研究问题与设计是否匹配（因果问题 vs 描述问题）。**[critical]**
- D3 对照/比较组是否明确（若适用）。
- D4 暴露/干预/变量定义是否清晰（若适用）。
- D5 主要结局是否预先定义。

### 2.2 证据充分性（evidence_sufficiency）

- E1 样本量与抽样方法是否报告，能否支撑结论。**[critical]**
- E2 统计方法与检验口径是否报告，且与设计匹配。**[critical]**
- E3 效应量与精度（置信区间/标准误）是否报告。
- E4 缺失数据/失访处理是否报告。
- E5 主要/次要结局是否区分，是否存在选择性报告。

### 2.3 偏倚风险（bias_risk，按文献类型选取适用领域）

- B1 选择偏倚（selection bias）。**[critical 偏倚领域]**
- B2 信息/测量偏倚（information/measurement bias）。
- B3 混杂偏倚（confounding）。**[critical 偏倚领域]**
- B4 失访/报告偏倚（attrition/reporting bias）。
- B5 发表偏倚（publication bias，系统综述适用）。

偏倚条目必须落在"该领域在本文献中的具体表现"，逐条说明，而非一句笼统"存在偏倚"。

### 2.4 报告完整性（reporting_completeness）

- R1 方法是否足以复现（样本/入排/变量/统计方法齐全）。
- R2 结果与结论是否一致（不夸大、不过度外推）。**[critical]**
- R3 研究局限是否声明。
- R4 利益冲突/资助是否声明。
- R5 图表/表格与正文一致，关键图表可解析。

### 2.5 文献类型特有检查点（追加）

- RCT：随机化方法、分配隐藏、盲法、按意向治疗分析（ITT）。
- 队列：随访时长、失访率、暴露测量时点。
- 病例对照：病例与对照的来源与选择、匹配、回忆偏倚控制。
- 系统综述/Meta：检索策略、纳入排除标准、偏倚评估工具、异质性检验。
- 横断面/描述性：抽样框架、应答率、测量工具信效度。

## 3. 证据优先顺序

1. 方法/结果章节原文 > 摘要 > 讨论/结论（摘要最易与正文不一致）。
2. 表格/图表编号（如 `Table 2`）> 叙述性文字。
3. `quote` 必须是实际输入文本中的逐字片段，禁止改写、概括或补全；`locator` 用 `scripts/litqa.py locators` 生成的稳定定位（`<file>#p<序号>`）或页面/章节/表号。
4. 全文不可得、仅摘要时，相关条目一律 `insufficient_evidence`，并在 `limitations` 记录，不得按摘要"看起来合理"地补缺。

## 4. 分级阈值（`scripts/litqa.py grade` 严格实现）

设：`bad_crit` = critical 条目中 verdict ∈ {contradicted, not_reported} 的数量；`bias_present_crit` = B1/B3 偏倚领域 verdict=present 的数量；`bad_any`、`bias_present`、`bias_possible`、`bias_insufficient` 同理为全量计数；`crit_insuff/crit_total` = critical 条目中证据不足占比。

| 等级 | 触发条件（自上而下，首个命中即定级） |
|---|---|
| `high` | `bad_crit > 0` 或 `bias_present_crit > 0` |
| `some` | `bad_any > 0` 或 `bias_present > 0` 或 `bias_possible ≥ 2` 或 `bias_insufficient ≥ 3` |
| `unclear` | 无可用评价条目，或 `crit_total > 0` 且 `crit_insuff / crit_total ≥ 0.5` |
| `low` | 其余情况（关键条目均有证据支持、无明确/可能存在偏倚、无未报告关键项） |

`grade` 在 `result.json` 中输出 `risk_level / rationale / counts / thresholds`，并追加一条 `change_log` 事件，说明等级由各维度计数汇总而来，不给出孤立标签。

## 5. 弃权与冲突规则

- 原文未报告且该类型要求报告 → `not_reported`（计入分级）；不适用 → `not_applicable`（不计入）。
- 证据不足 → `insufficient_evidence`/`insufficient`，不得升级为任何确定结论。
- 同一指标多来源不一致：全部记录到 `sources` 与 `uncertainty`，标 `source_conflict`，判定取"更保守"档并说明。
- 缺失元数据（DOI、页码、作者等）写 `not_reported` 或 `pending_human`，不虚构。

## 6. 边界与价值（相对已有方案）

- **相对通用摘要 Agent**：不输出"设计合理、样本适中"式整体评语，而是把结论拆成受控判定词（§1）+ 逐字证据锚点（§3），复核者无需重读全文即可逐条验证。
- **相对公开报告清单（PRISMA/STROBE/CONSORT 等）**：报告清单回答"该报告什么"，本量表进一步回答"该判断依据原文哪一句、证据不足怎么办"，并把弃权（`insufficient_evidence`/`not_reported`）做成显式、可计数的状态，而不是默认通过。
- **相对期刊编辑初筛**：编辑初筛偏重是否符合规范，本量表把偏倚风险拆到具体领域（§2.3）并给出可复现的分级阈值（§4），同一输入可得到同一等级。
- **边界**：本 Skill 不替代同行评审、事实核验、临床/政策决策或研究结论；只在一个明确的文献类型与四类维度上做可复核评价，不覆盖全部学科与全部设计。
