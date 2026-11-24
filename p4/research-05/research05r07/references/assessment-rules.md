# 四项交付判定口径与证据字段

本文件规定 `research_question / prior_work / novelty / feasibility / search_protocol`
四项交付的判定口径、字段与证据要求。所有结论必须回到材料位置（locator + 逐字 quote）。

## 1. 研究问题定义（research_question）

把模糊设想拆成可检验陈述。字段：

- `raw`：材料中原始问题表述（有出处则附 locator）。
- `object`：研究对象。
- `variables`：关键变量（自变量/因变量/控制变量）。
- `baseline`：比较基线。
- `expected_outcome`：预期结论。
- `falsifiable_condition`：可证伪条件（什么观察会推翻结论）。
- `testable_statement`：一句可检验陈述，**必须包含对象/变量/基线/可证伪条件**。
- `status`：`complete | partial | missing`。
- `gaps`：材料未给出的缺口。

判定口径：缺对象/变量/基线/可证伪条件任一项 → `partial`；全部缺失 → `missing`。

## 2. 已有工作比较（prior_work）

比较矩阵字段（每篇相近工作一行）：

- `work`：文献标识（用材料内出现的标识，不造 DOI）。
- `task` / `method` / `data` / `evaluation` / `conclusion`。
- `comparability`：`directly_comparable | partially_comparable | not_directly_comparable`。
- `incomparability_reason`：不可比原因（数据、任务、评价方式差异等）。

判定口径：只比较**真正相近**的工作；禁止跨文档串值（不同文献的数值/指标不得拼成一行）。

## 3. 创新点识别（novelty）

每条创新陈述字段：

- `claim`：创新陈述原文。
- `type`：`method | data | scenario_transfer | conclusion_revision`（四选一）。
- `verdict`：`novel | covered | partially_covered | unverifiable`。
- `covered_by`：被哪条已有工作覆盖（locator + quote）。
- `reason`：判定理由。
- `evidence`：支持/反驳证据回链。

判定口径：场景迁移、结论修正**不得**标为方法创新；`covered_by` 空且无证据 → `unverifiable`，不默认 novel。

## 4. 可行性判断（feasibility）

每条前提字段：

- `prerequisite`：前提名（数据、算力、实验条件、时间窗口、人员能力等）。
- `evidence_status`：`reported | assumed | missing | conflicting`（四选一）。
- `source`：前提来源 locator + quote；`assumed` 必须显式标注。
- `note`：解释。
- 汇总字段：`conclusion`（带条件的可行性结论）、`risks`（失败风险列表）。

判定口径：`assumed` 未标注来源即视为不可核验；`missing` 不视为"不存在"。

## 5. 检索协议（search_protocol）

字段：`queries`（检索式）、`inclusion_criteria`（纳入规则）、`exclusion_criteria`（排除规则）、
`sources_searched`、`screened/included/excluded`（数量）、`notes`。

判定口径：检索式与纳排规则可独立复核；"检索不到"写 `not_reported`，不写成"尚无人研究"。

## 6. 证据字段通用要求

`evidence_matrix` 每行含：`source_id`、`locator`、`quote`（逐字）、`field`（支撑哪个字段）、`status`。

- `locator`：如 `materials/paper.txt` 行号、`task.md#L3`。
- 无引用信息 → `pending_human` 或 `not_reported`，不造 DOI/数值。
