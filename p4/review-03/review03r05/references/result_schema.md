# result.json 输出契约

`/app/output/result.json` 是机器可复核的主产物；`/app/output/report.md` 是人类可读、
可编辑版本。两者由脚本从同一份数据渲染，内容一致。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json` 的 `case_id`，缺失时为 `unknown` |
| `status` | enum | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `result_status` | enum | `通过` \| `问题` \| `待复核` \| `未检查` |
| `summary` | object | `verdict` + `overall_assessment` + `counts` + 按 facet/finding_type/check_class 分类计数 |
| `review_report` | object | `findings[]` + `checked_facets[]` + `unchecked_facets[]` |
| `evidence` | object | `evidence_map`：每条 finding 的定位+摘录+依据 |
| `contract_alignment` | object | output_contract 逐字段对齐说明 |
| `output_contract` | any | 题目 output_contract 原样保留 |
| `sources` | array | 实际核验的材料清单（path/kind/role/status） |
| `uncertainty` | array | 不确定项 |
| `pending_human` | array | 待人工复核项 |
| `limitations` | array | 局限与未完成项 |
| `errors` | array | 读取/解析错误 |
| `degradations` | array | 降级记录（缺软件、扫描版、未解析图表等） |
| `numeric_checks` | array | 数值一致性候选（待人工复核） |
| `change_log` | array | 变更记录：每次落盘的时间、阶段、动作、细节 |
| `runtime` | object | `started_at`/`finished_at`/`elapsed_seconds`/`model_requests_used` |
| （contract 字段） | any | output_contract 的每个字段原样回填到顶层 |

## 2. output_contract 对齐（本版重点）

`scaffold.py` 与 `finalize.py` 均调用同一对齐逻辑：

1. 读取 `/input/case.json` 的 `output_contract`。
2. 原始对象原样存入 `result.output_contract`。
3. 逐字段回填到顶层：已有规范字段（如 `case_id`/`status`）保留规范值并记录
   `canonical_present`；缺失字段补默认值（`null`/`[]`/`""`）并记录
   `added_with_default`。不改名、不删除未知键。
4. `contract_alignment` 记录 `received`（原始契约）、`fields[]`（逐字段落点）、
   `unknown_top_level_preserved[]`（保留的未知顶层键）。
5. 数值类默认给 `null`，字符串给 `""`，数组给 `[]`，对象给 `{}`——绝不编造具体数值。

`finalize.py` 合并 findings 时从 case.json 重新对齐，保证即使跳过 scaffold 也不会丢失
contract 字段；同时保留上一版 result.json 的 `change_log` 与 `runtime.started_at`。

## 3. finding 字段（`review_report.findings[]`）

必填六项：`id / facet / locator / quote / severity / suggestion`。

| 字段 | 说明 |
|---|---|
| `id` | 稳定编号，如 `F001` |
| `facet` | 五个检查面之一 |
| `check_class` | 六类审查面之一 |
| `finding_type` | 五类缺陷类型之一 |
| `severity` | 五档严重度之一 |
| `claim` | 审查主张：可复核的一句判断 |
| `locator` | 可回查定位串（`p.3 ¶2` 或 `materials/x.txt ¶2`） |
| `quote` | 逐字摘自原文 |
| `basis` | `{rule_id, rule}`：触发的规则（引用 checklist.md 小节号） |
| `suggestion` | 修改建议 |
| `confidence` | `high` \| `medium` \| `low` |
| `review_status` | `confirmed` \| `needs_review` |
| `related_locators` | 关联定位（冲突双方都要记录） |

"claim—evidence—basis"三元组是本版的复核结构：复核人只做三件事——`claim` 是否成立、
`quote` 是否逐字、`rule` 是否适用。任何一项对不上，该条即需复核。

## 4. 合法枚举值汇总

- facet：`research_design` `variable_definition` `statistical_methods`
  `sample_size_power` `results_reporting`
- check_class：`fact_check` `citation_support` `method_design`
  `statistical_reporting` `argumentation_chain` `academic_norms`
- finding_type：`factual_error` `insufficient_evidence` `normative_issue`
  `advisory` `pending_review`
- severity：`critical` `major` `minor` `advisory` `pending`
- status：`ok` `partial` `insufficient_evidence` `input_error`
- review_status：`confirmed` `needs_review`

## 5. numeric_checks 条目

`{id, kind, locator, candidate, status, finding_id}`。`kind` ∈
`p_vs_wording` `p_vs_ci` `n_inconsistency`；`status` 默认 `candidate`，可由
`meta.json.numeric_check_resolutions` 覆写为 `confirmed`/`rejected` 并关联
`finding_id`。候选不是结论，必须逐条复核。

## 6. 诚实性要求

- 材料不足 → `status=insufficient_evidence`；缺 case.json 且无材料 → `input_error`。
- 所有缺失写 `not_reported`/`not_verified`，禁止虚构引用、DOI、URL、数值、软件状态
  或评测分数。
- 冲突信息两条都记录并标 `source_conflict`，进 `uncertainty`。
- 任何中断都保留当前合法 partial 产物，并在 `limitations` 写明未完成项。
