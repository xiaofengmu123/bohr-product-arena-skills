# 选题评估判定口径与字段枚举

本文档是 `SKILL.md` 的执行细则：四项交付的判定口径、字段枚举与证据状态分层。Agent 写中间产物时必须遵守这些枚举值，保证 `finalize` 与复核器能一致校验。

## 1. 证据状态（`evidence_model.json` 中 `status`，也用于各交付的 `status`）

| 值 | 含义 | 使用条件 |
|---|---|---|
| `reported` | 材料事实，有逐字 quote | 材料明确写出的对象、变量、数值、方法、结论 |
| `inferred` | 合理推断，不是材料原话 | 由多处材料综合推出，须在 `reason` 说明推理链 |
| `assumed` | 显式假设 | 材料未报告、但判断成立需要的前置条件 |
| `missing` | 材料未报告该项 | 不得写 `reported`，写入 `gaps` 并降级 |
| `conflicting` | 两处材料结论相反 | 同时登记两方到 `conflicts` 并给取舍理由 |

"未报告"≠"不存在"；"检索不到"≠"尚无人研究"。这两类都只能写 `missing`/`unverifiable` 并保留在 `uncertainty.gaps`，不得写成否定性结论。

## 2. 研究问题定义（`plan.json → research_question`）

必填字段与判定口径：

- `object`：研究对象（哪一群体/系统/对象）。
- `variables`：关键变量（自变量、因变量、混杂变量），至少列出核心两个。
- `baseline`：比较基线（对照方法/历史值/现状），没有就标 `missing`。
- `expected_outcome`：预期结论的方向与量级（能写多少写多少）。
- `falsification_condition`：可证伪条件——什么观察结果能推翻结论。
- `testable_statement`：合并以上要素的一句可检验陈述。
- `status`：`complete / partial / missing`；`gaps` 列出缺的要素。

判准：`testable_statement` 含对象、变量、基线、可证伪条件四要素才算 `complete`；缺任一项为 `partial`；三项以上缺失为 `missing`。

## 3. 检索协议（`plan.json → search_protocol`）

- `queries`：实际使用的检索式（原文照录，便于复核）。
- `inclusion_criteria` / `exclusion_criteria`：纳排规则，逐条可执行。
- `scope`：检索范围（材料集合/数据库/时间窗/语言）。
- `screening_steps`：去重、标题筛、全文筛的步骤顺序。
- `limitations`：检索覆盖不到的空白（如仅限给定材料、无外部数据库）。

## 4. 已有工作比较（`figure_model.json → comparison_matrix`）

比较矩阵列固定为 `task / method / data / evaluation / conclusion`，每行字段：

- `row_id`、`work`（工作标识，用材料内名称）、`source_id`。
- `task / method / data / evaluation / conclusion`：均只填该材料自报的值，**禁止跨文档串值**。
- `comparable`：是否与本题真正相近（任务+评价方式一致才算相近）。
- `non_comparable_reason`：不可直接比较时写明原因（数据不同、任务不同、评价口径不同）。
- `evidence_ids`：该行每个取值对应的证据记录 id。

判准：比较对象选对（真正相近）才有价值；选了不同任务的工作必须写清"为何仍列出/为何排除"。

## 5. 创新点识别（`figure_model.json → novelty_claims`）

`type` 枚举：`method`（方法创新）、`data`（数据创新）、`scenario_transfer`（场景迁移）、`conclusion_revision`（结论修正）。

`verdict` 枚举：`novel`（未被覆盖）、`covered`（已被覆盖）、`partially_covered`（部分覆盖）、`unverifiable`（材料不足无法判定）。

每条必含 `covered_by`（被哪些 `row_id` 覆盖）与 `reason`。**硬规则**：场景迁移、结论修正不得判为 `method` 创新；"材料未报告该方法"只能判 `unverifiable`，不得据此判 `novel`。

## 6. 可行性判断（`figure_model.json → feasibility_premises`）

`aspect` 枚举：`data`（数据）、`compute`（算力）、`experiment_condition`（实验条件）、`time_window`（时间窗口）、`personnel`（人员能力）。

每条必含：`statement`、`status`（`reported/assumed/missing/conflicting`）、`risk`（失败风险描述）、`evidence_ids`。`assumed` 必须显式写"假设"。`figure_model.json` 顶层 `conditional_conclusion` 汇总：在哪些前提成立下结论为可行，任一前提缺失时结论如何退化。

## 7. 综合与冲突（`synthesis.json`）

- `conclusions`：与 `research_question`、证据范围一致的综合结论。
- `research_gaps`：材料未覆盖但能定位的研究空白。
- `boundary_statement`：明确结论只到证据范围为止，不扩展到未报告项。
- `conflicts[]`：`topic` + 两方记录（各带 `evidence_ids`）+ `resolution` + `reason`。相反证据不得隐藏。
- `uncertainty`：`biases`（偏倚）、`gaps`（空白）、`undetermined_items`（无法确定项）。
- `pending_human`：需要人工确认的事项；`limitations`：本方法/材料的限制。

## 8. 证据记录（`evidence_model.json`）

每条：`evidence_id`（`e001` 递增）、`source_id`（对应 `evidence_index.json`）、`locator`（如 `materials/paper.pdf` 抽取文本 `#L12` 或 `task.md#L3`）、`quote`（逐字原文，不得改写）、`claim`（该证据支撑的判断）、`field`（`question|prior_work|novelty|feasibility|synthesis`）、`status`。

`locator` 必须能实际定位到文件中的某一行；`quote` 必须能在该位置原文中找到。无引用信息写 `pending_human` 或 `not_reported`，不造 DOI/数值。

## 9. 比较矩阵示意行（字段示例，非正式案例）

以下仅演示 `comparison_matrix` 一行的字段形态与"不可直接比较"如何标注，不声称任何真实文献结论：

```json
{
  "row_id": "w01",
  "work": "示例工作（占位）",
  "source_id": "src01",
  "task": "材料所述任务",
  "method": "材料所述方法",
  "data": "材料所述数据/规模；未报告则填 not_reported",
  "evaluation": "材料所述评价方式",
  "conclusion": "材料所述结论（原文口径，不跨文档串值）",
  "comparable": false,
  "non_comparable_reason": "评价方式不同：该工作未给出与本问题同口径的比较基线",
  "evidence_ids": ["e001", "e002"]
}
```

复核顺序建议：先看 `non_comparable_reason` 是否成立，再看五列取值是否都能在 `evidence_ids` 指向的 `quote` 中逐字找到；任何一列找不到即退回 `unverifiable` 或 `not_reported`，不得补写。

