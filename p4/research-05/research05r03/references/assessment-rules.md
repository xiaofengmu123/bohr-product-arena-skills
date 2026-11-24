# 评估口径与证据字段（assessment-rules.md）

本文是 SKILL.md 四项交付的判定口径。Agent 按此写 fragment，`scripts/core_impl.py` 的
`validate()` 按此校验。所有关键结论必须能回到材料原文（locator + 逐字 quote）。

## 1. 研究问题定义（research_question）

把模糊设想拆成五要素，缺一项即标 `status=partial` 并写入 `gaps`：

| 要素 | 字段 | 口径 |
|---|---|---|
| 研究对象 | `object` | 材料明示的实体/人群/样本，不许外推 |
| 关键变量 | `variables` | 干预/暴露 + 结局，至少各一 |
| 比较基线 | `baseline` | 与什么比：对照组/基线模型/现状 |
| 预期结论 | `expected` | 可被数据支持或否定的方向性表述 |
| 可证伪条件 | `falsification` | 什么观测会推翻该结论 |

`testable_statement` 必须同时含对象、变量、基线、可证伪条件四者；只写对象不带
可证伪条件，算 `partial`，不得标 `complete`。材料完全没给问题 → `status=missing`。

## 2. 已有工作比较（prior_work）

比较矩阵每行固定字段：`work_id / task / method / data / evaluation / conclusion /
comparability / incomparable_reason`。

- `task` 只写该工作做的任务，禁止把材料里不同文档的数值串到同一行。
- `comparability` 取 `directly_comparable / partially_comparable / not_comparable`。
- `incomparable_reason` 必须具体（任务不同、数据集不同、无同口径指标），不能只写
  “不可比”。
- `nearest_works` 只列真正相近的工作；相近 ≠ 关键词重叠，须任务与方法同时相近。

## 3. 创新点识别（novelty）

`category` 只能取四类：`method`（方法创新）/ `data`（数据创新）/ `scenario`（场景迁移）/
`conclusion`（结论修正）。场景迁移与结论修正**不得**标为方法创新。

`verdict` 只能取：`novel / covered / partially_covered / unverifiable`。每条必须给：

- `covered_by`：哪条已有工作覆盖了它（`novel` 时可给最接近者并说明差异）；
- `rationale`：判定理由，引用 `evidence_matrix` 的 evidence_id；
- `unverifiable`：材料不足以判断，写进 `uncertainty.unverifiable`，不得写成 `novel`。

## 4. 可行性判断（feasibility）

每条前提固定字段：`item / category / state / basis / evidence_refs`。

- `category`：`data / compute / experiment / time / personnel / other`。
- `state`：`reported`（材料有出处）/ `assumed`（显式假设，必须标）/ `missing`
  （未报告）/ `conflicting`（材料间矛盾）。
- `assumed` 的 `basis` 必须写“假设：…”，并在 `uncertainty.gaps` 登记，不得当作事实。
- `conditional_conclusion` 写成“若 A 且 B 成立，则可行；否则风险 C”的条件式；
  `failure_risks` 逐条列出失败模式。

## 5. 证据矩阵（evidence_matrix）

每行字段：`evidence_id / source_id / locator / quote / field / supports / status`。

- `locator` 形如 `materials/paper.pdf#p3` 或 `task.md#L3`，指向材料内位置；
- `quote` 必须逐字复制材料原文（含标点），不得改写、拼接；
- `field` 指明该证据支撑哪项交付（如 `novelty.claims[0]`）；
- `supports` 取 `supports / contradicts / context`。

无来源的结论不写进 evidence_matrix，改写 `pending_human` 或 `not_reported`，
禁止虚构 DOI、数值或引用。

## 6. 冲突证据（conflicts）

同一指标/结论出现相反证据时，双方都登记：`topic / side_a / side_b / resolution`。
`resolution` 必须写取舍理由（样本量、方法强度、时间先后、指标口径），
“未报告” ≠ “不存在”：检索不到只写 `uncertainty.gaps`，不写“尚无人研究”。

## 7. 状态推导（与 core_impl.derive_status 一致）

| 状态 | 条件 |
|---|---|
| `input_error` | case.json 缺失或输入目录缺失 |
| `insufficient_evidence` | 有 case.json 但无任何可读材料 |
| `ok` | 四项交付齐全且 evidence_matrix 非空 |
| `partial` | 其余情况 |

`status` 由 `finalize` 按内容推导，不由手写指定；证据行缺 locator/quote 时
`ok` 自动降为 `partial` 并登记 `errors`。

## 8. 边界与诚实性

- 结论只到证据范围为止；`synthesis.boundary_statement` 明确适用对象、指标与条件。
- 推断写 `inferred`，不写成事实；分不清就写 `not_reported` 或进 `pending_human`。
- 偏倚（来源单一、发表偏倚、样本偏差）、空白、无法确定项全部进 `uncertainty`。
