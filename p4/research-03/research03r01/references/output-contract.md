# 输出契约与组装规则

本文件定义 `/app/output/result.json` 的字段、类型与生成方式，供编写 Skill 的
Agent 与复核者对照。脚本 `scripts/main.py` 是这些规则的唯一确定性实现；手工
只写 `/app/work/` 下的内容，不手工改 JSON。

## 1. 顶层必需字段

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `case_id` | string | `skeleton` 从 `case.json` 提取 | `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown` |
| `status` | string | `skeleton`/`finalize` | `ok \| partial \| insufficient_evidence \| input_error` |
| `sources` | array | `finalize` | 文献来源注册表，每条含 `source_id`、`file`，可选 `title/year/authors/doi/type/abstract_only` |
| `uncertainty` | object | `synthesis.json` 或 `finalize` | 冲突、偏倚、空白、无法确定项 |
| `change_log` | array | 脚本自动追加 | `{ts, action, detail}`；记录 skeleton/merge/finalize |
| `limitations` / `errors` / `degradations` / `pending_human` | array | 脚本与 `synthesis.json` | 诚实标注缺口与故障 |
| `runtime_seconds` | number | `finalize` | 从 `run_state.started_at` 起算 |

## 2. 领域字段（五个环节）

### 2.1 问题与检索（P1，`/app/work/protocol.json`）

- `review_question`: string，非空。
- `subquestions`: `[{id, text, depends_on?[]}]`，≥1（推荐 ≥2）。
- `search_strategy.query_blocks`: `[{id, database, query, intent, fields?, time_limit?, language_limit?, run_status?, hits?}]`。
  每个块必须写清数据库、可执行检索式（布尔逻辑、同义词/受控词）、意图与时间/语言限制；`run_status=not_run` 用于数据库不可用时不伪造命中数。

### 2.2 纳排标准（P1，先于筛选落盘）

- `inclusion_criteria` / `exclusion_criteria`: `[{id, text, applies_to?, priority?, conflict_rule?}]`。
  必须条款化、可判定，并给出冲突处理规则（例如“排除优先于纳入”）。

### 2.3 两轮筛选（P2，`/app/work/screening.jsonl`）

- 每行：`{source_id, round: title_abstract|fulltext, decision: include|exclude|maybe, reason, criterion_id, judge, duplicate_of?, note?}`。
- 排除必须带 `reason` 与 `criterion_id`（须命中上述标准 id）；去重记 `duplicate_of`。
- `prisma_flow` 由脚本按 `recompute_prisma` 从记录重算：`identified / deduplicated / screened_title_abstract / excluded_title_abstract / screened_fulltext / excluded_fulltext / included`。

### 2.4 质量评价（P3，`/app/work/quality_appraisal.jsonl`）

- 每行：`{source_id, tool, dimensions: [{dimension, rating, basis_locator, quote}], overall, impact_on_conclusions}`。
- 每个维度都要给出依据位置与逐字 quote；`impact_on_conclusions` 说明该等级如何影响综合结论。

### 2.5 证据与综合（P3/P4）

- `evidence_map.evidence`: `evidence.jsonl` 的全部证据行（见 `evidence-protocol.md`）。
- `evidence_map.by_source`: 脚本按 `source_id` 聚合；`by_conclusion`: 脚本按结论 id 聚合 `evidence_ids`；`conflicts`: 与 `synthesis.conflicts` 一致。
- `synthesis.conclusions`: `[{conclusion_id, statement, evidence_ids[], support_level, boundary, basis[{evidence_id, role, locator, quote}]}]`。
- `synthesis.conflicts`: 每条 ≥2 个 `evidence_ids` 并列；`synthesis.gaps`: 落到具体缺口。

## 3. `output_contract` 回填规则

- `case.json` 的 `output_contract`（或 `outputContract`）可能是 dict（字段→类型）、list（字段名）或缺省。
- `skeleton` 把契约中**不在默认领域字段内**的键以空占位符（`null`/`[]`/`{}`）加入 result.json，不改名、不删未知键。
- `selfcheck` 的 `contract_fields` 检查逐键比对 `run_state.contract_spec`，缺一键即失败。

## 4. 确定性规则

- 所有列表在 `finalize` 按稳定键排序：evidence 按 `evidence_id`、screening 按 `source_id`、sources 按 `source_id`、appraisal 按 `source_id`。
- JSON 以插入序写出（`ensure_ascii=False, indent=2`）；同一输入两次运行结果一致。
- 证据 `quote` 核对只做空白归一化（`\s+` → 单个空格、去首尾、小写），不改变逐字要求。

## 5. 最小合法产物（异常场景）

任何异常下都要写出合法 JSON 并退出码 0：

- 空输入 → `status=insufficient_evidence`，`limitations` 说明，其余字段为空占位。
- `case.json` 缺失/坏 JSON → `partial`/`input_error`，契约字段回退默认。
- 检索为空 / 全文不可得 / 证据矛盾 → 相应字段标 `not_run`/`abstract_only`/`undetermined`，记 `degradations`。
