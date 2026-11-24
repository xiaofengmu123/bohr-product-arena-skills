# 判定口径与证据字段（assessment-rules）

本文是 SKILL.md 第 4 节执行流程的判定依据。所有结论按此口径形成，并回落到
`evidence_map.claims`。判定口径的作用是让"四项交付"在同一套标准下可复核，
避免 Agent 每次自由发挥、同一输入结论漂移。

## 0. claim_id 约定

| 前缀 | 交付 | 示例 |
|---|---|---|
| `rq` | research_question | `rq-1` |
| `sp` | search_protocol | `sp-1` |
| `pw` | prior_work | `pw-1` |
| `nv` | novelty | `nv-1` |
| `fb` | feasibility | `fb-1` |
| `syn` | synthesis | `syn-1` |
| `cf` | conflicts | `cf-1` |

- 编号在同一 case 内稳定、唯一；交付对象通过 `claims:[...]` 引用，不复制证据。
- 每条 claim 只陈述**一个**断言；复合句拆成多条。

## 1. 研究问题定义（research_question）

把模糊设想拆为五项，缺一项即标 `status=partial` 并把缺口写入 `gaps`：

- `object`：研究对象/干预（具体到方法、人群、系统或现象）。
- `variables`：关键变量与指标（含测量口径）。
- `baseline`：比较基线（与谁比、什么条件下比）。
- `expected_outcome`：预期结论（方向与幅度，尽量带量）。
- `falsification_condition`：可证伪条件——出现什么观测即可判定该问题陈述被否定。

`testable_statement` 必须同时含对象/变量/基线/可证伪条件；材料只给部分时，
缺失项写 `not_reported`，不得替作者补写对象或数值。

## 2. 检索协议（search_protocol）

- `query`：实际检索式（关键词/布尔式），不得省略。
- `inclusion_criteria` / `exclusion_criteria`：纳排规则，逐条可执行。
- `screened` / `included` / `excluded_with_reasons`：记录检索覆盖面与排除原因。
- 材料范围内"检索不到"只能写"本材料集未检索到"，不得写成"尚无人研究"。

## 3. 已有工作比较（prior_work）

`comparison_matrix` 每行至少含：`work_id`、`source_id`、`task`、`method`、
`data`、`evaluation`、`conclusion`、`comparable`、`incomparable_reason`。

- 只对**真正相近**的工作建行；字段值逐字取自材料，禁止跨文档串值。
- `comparable=false` 时必须在 `incomparable_reason` 写明不可比原因
  （任务不同 / 数据不同 / 指标不同 / 条件不同），否则默认按可比处理。

## 4. 创新点识别（novelty）

每条创新陈述先分类 `type`：`method`（方法创新）/ `data`（数据创新）/
`scenario_transfer`（场景迁移）/ `conclusion_revision`（结论修正）。

`verdict` 判定：

| verdict | 含义 |
|---|---|
| `novel` | 材料内最近工作均未覆盖该点，且有证据表明差异 |
| `covered` | 已有工作已明确覆盖，给出 `covered_by`（work_id） |
| `partially_covered` | 部分覆盖，说明覆盖了哪一部分、缺哪一部分 |
| `unverifiable` | 材料不足以判定，登记 `uncertainty` |

硬规则：场景迁移与结论修正**不得**判定为 `method` 类方法创新；"未检索到"不得
写成 `novel`（最多 `unverifiable`）。

## 5. 可行性判断（feasibility）

逐条核查 `data / compute / experiment / time / personnel` 前提，每条标：

- `reported`：材料明确给出（必须回链 locator+quote）。
- `assumed`：合理假设（**显式标注**，写入 `uncertainty`，不得伪装成事实）。
- `missing`：材料未报告。
- `conflicting`：不同来源矛盾，同步登记 `conflicts`。

`conditional_conclusion` 必须以"若……则……"表述，并列出 `failure_risks`。
任何前提不给来源又未标 `assumed`，视为 `missing`。

## 6. 证据字段（evidence_map）

`evidence_map.claims[].evidence[]` 每项字段：

- `source_id`：对应 `sources` 清单中的 id。
- `locator`：`<relpath>#L<n>`（文本行）/ `<relpath>#p<n>`（PDF 页）/
  `task.md#L<n>`；不得为空或 `"?"`。
- `quote`：**逐字**取自材料的原文片段，含标点；不得改写、合并跨段内容。
- `relation`：`supports`（支持）/ `contradicts`（相反）/ `contextualizes`（背景）。
- `quote_status`：`verified`（逐字核对）/ `inferred`（由材料推断）/
  `missing`（找不到原文）/ `conflicting`（多源冲突）。

`quote_status` 为 `missing/not_reported` 时 `quote` 留空，并把该 claim 同步写入
`uncertainty.gaps` 与 `pending_human`；**禁止编造 quote、DOI 或数值**。

## 7. 冲突证据（conflicts）

同一主题出现相反证据时，两方都登记：

```json
{
  "topic": "…",
  "side_a": {"source_id": "…", "locator": "…", "quote": "…"},
  "side_b": {"source_id": "…", "locator": "…", "quote": "…"},
  "resolution": "取舍理由（依据样本/条件/方法差异给出，缺依据则写未定）"
}
```

"未报告"≠"不存在"；无法取舍时 `resolution="unresolved"` 并进 `pending_human`。

## 8. 结论强度措辞

- 材料直接支持 → 事实（`fact` / `established`）。
- 由材料推断 → `inference` / `inferred`，标注 `confidence`。
- 无法确定 → `gap` / `unconfirmed`，不得升级为事实。
