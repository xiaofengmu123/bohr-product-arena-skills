# 四项交付判定口径与证据字段

本文件给出结果中各项交付的字段、取值词表与判定口径。规格 fragment 写入
`/app/work/spec/` 时按下表命名；`build` 会把这些键原样合并进 `result.json`。

## 0. 证据账本记录（`ledger.jsonl` 每行）

| 字段 | 含义 | 取值/要求 |
|---|---|---|
| `id` | 证据编号 | `E001` 起，自动分配或 `--id` 指定；novelty/feasibility/synthesis 用 `evidence_ids` 引用它 |
| `source_id` | 材料编号 | `scan` 分配（`m01`, `m02`…），与 `material_inventory.json` 一致 |
| `locator` | 定位 | `materials/paper.txt:42`、`task.md#L3`、`paper.pdf#p3` 等形式 |
| `quote` | 逐字引文 | 必须逐字；`build` 会做空白归一子串匹配，≥8 字符才可核验 |
| `facet` | 支撑哪类判断 | `research_question / prior_work / novelty / feasibility / conflict / gap / general` |
| `status` | 证据类型 | `fact`（材料事实）/ `inference`（合理推断）/ `conflict`（相反证据） |
| `note` | 说明 | 可选 |

## 1. `research_question`（研究问题定义）

| 字段 | 要求 |
|---|---|
| `testable_statement` | 一句话可检验陈述，含对象、关键变量、比较基线、预期结论、可证伪条件 |
| `object` | 研究对象 |
| `variables` | 关键变量（列表） |
| `baseline` | 比较基线 |
| `expected` | 预期结论 |
| `falsifiable_condition` | 可证伪条件（什么观测结果会推翻结论） |
| `status` | `complete / partial / missing`；材料没给齐必须非 complete 并列出缺口 |
| `gaps` | 缺口清单（可选，status 非 complete 时必填） |

判定：只有对象/变量/基线/可证伪条件齐备才算 `complete`。

## 2. `search_protocol`（检索与纳排规则）

| 字段 | 要求 |
|---|---|
| `scope` | 检索范围（库、时间窗、语言） |
| `query` / `queries` | 检索式 |
| `inclusion` | 纳入规则 |
| `exclusion` | 排除规则 |
| `screening` | 筛选步骤与依据 |

只写本次实际采用/可执行的规则；没做的检索不要写"已检索"。

## 3. `prior_work`（已有工作比较）

比较矩阵每条至少含：`task`（任务）、`method`（方法）、`data`（数据）、
`metric`（评价方式）、`conclusion`（结论）、`source` 或 `evidence_ids`。

| 附加字段 | 含义 |
|---|---|
| `comparable` | `comparable / partial / not_comparable` |
| `not_comparable_reason` | 不可直接比较的原因 |

禁止跨文档串值：某条记录的字段只能来自同一个 `source_id`/`evidence_ids`。

## 4. `novelty`（创新点识别）

每条创新陈述：

| 字段 | 取值 |
|---|---|
| `claim` | 创新点陈述 |
| `type` | `method / data / scenario_transfer / conclusion_revision`（方法/数据/场景迁移/结论修正） |
| `verdict` | `novel / covered / partially_covered / unverifiable` |
| `covered_by` | 覆盖它的已有工作（evidence id 或材料定位）；`novel` 也要写明"最近工作中未发现覆盖"的依据 |
| `evidence_ids` | 支撑判定/覆盖判定的证据 id 列表 |
| `reason` | 判定理由 |

场景迁移、结论修正**不得**写成方法创新；`verdict=novel` 必须能指出"相对最近工作"的边界。

## 5. `feasibility`（可行性判断）

每条前提：

| 字段 | 取值 |
|---|---|
| `premise` | 前提（数据、算力、实验条件、时间窗口、人员能力等） |
| `status` | `reported / assumed / missing / conflicting` |
| `evidence_ids` | 前提出处 |
| `note` | 说明；`assumed` 必须显式写"假设"与理由 |

结论应为**带条件的可行性结论**并列出失败风险；不能只写一句"技术路线可行"。

## 6. `synthesis` / `conflicts` / `uncertainty`

- `synthesis.conclusions`：结论列表，每条应可回链到 evidence id。
- `synthesis.research_gaps`：研究空白。
- `synthesis.boundary_statement`：只到证据范围为止的边界说明。
- `conflicts`：每条含 `topic`、两方记录（`side_a` / `side_b`）、`resolution`（取舍理由）。
- `uncertainty`：`bias`（偏倚）、`gaps`（空白）、`unresolved`（无法确定项）、`limitations`（限制）。

## 7. 诚实性词表

- `fact` / `inference` 必须区分；不确定的结论写 `inferred`，不得写成事实。
- "未报告"写作 `not_reported`；"无法判断"写作 `unverifiable` 或 `unresolved`。
- 无引用信息写 `not_reported` 或进 `pending_human`；不编 DOI、数值、分数、运行结果。
- `status` 取值：`ok / partial / insufficient_evidence / input_error`，与
  `errors / degradations / pending_human / limitations` 一致。
