# 输出契约与结果字段语义（contract）

本文件说明 `result.json` 的字段语义与“契约对齐”机制，供评审或复跑者独立核对。

## 1. 契约优先原则

- 若 `/input/case.json` 含 `output_contract`（键名容忍 `outputContract`/`output`/`contract`/`result_schema`/`schema`，或位于 `runtime`/`spec`/`harness` 下），脚本递归展开其全部字段路径，逐一回填到 `result.json`。
- 无显式契约时，默认回填三组：`experimental_plan`、`evidence_and_risks`、`confirmation_items`。
- 契约字段的来源优先级：标准三组（standard）→ Agent 在 analysis.json 的 `contract_fills` 提供（agent）→ 已有计算值（computed）→ 缺失占位（missing）。
- 缺失字段填 `null`（单数）或 `[]`（复数，以 `s` 结尾且非 `ss`），并在 `contract_alignment` 中标 missing、写入 `limitations`/`pending_human`；**不改名、不删未知键、不补造内容**。

## 2. result.json 顶层字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `case_id` | string\|null | 输入标识；缺省为 null 并如实保留 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `output_contract` / `output_contract_key` | any / string | 原样保存的契约与其来源键 |
| `contract_alignment` | object | 每个契约字段的 `{path, present, filled, value_type, source, note}` |
| `research_question` | object | `testable_question`、`variables{independent,dependent,controlled}`、`hypothesis` |
| `comparison_frame` | object | `objectives`、`hard_constraints`、`criteria` |
| `candidates[]` | array | 统一七类字段 + 五维评分 + cost/time/thresholds/score/evidence/risks |
| `comparison_matrix` | object | `{plans[], rows{字段: {方案: 值}}}`，所有方案同一套行 |
| `selection` | object | `chosen`、`ranking`、`rationale`、`rejected[]`、`sensitivity` |
| `experimental_plan` / `evidence_and_risks` / `confirmation_items` | object/array | 默认三组契约的落点 |
| `confirmations` | array | 安全/伦理/设备/专家确认项，未确认标 pending |
| `sources` | array | 每条 `{locator, quote, kind}` |
| `uncertainty` / `pending_human` / `errors` / `degradations` / `limitations` | array | 诚实记录缺口与降级 |
| `runtime` | object | 开始/结束时间与引擎标识 |
| `artifacts` | array | 产出的相对路径与类型 |
| `change_log` | array | 只追加记录 `{seq, time, step, note}`，seq 单调 |

## 3. 成本与时间口径

- 成本六项：`equipment_time`（机时）、`consumables`（耗材）、`labor`（人力）、`sample_prep`（样品准备）、`scheduling`（排期）、`rework`（返工）。
- 每项为 `{amount, currency, kind, note, evidence}`；`kind` ∈ `once`（一次性）| `per_run`（按次消耗），按次项乘 `n_runs`。
- 时间五项：`preparation`（准备）、`scheduling`（排期）、`execution`（执行）、`queue`（表征排队）、`rework`（返工），单位小时。
- 任一必需值缺失 → `method=not_computable`、`total=null`，写入 `pending_human`，不强行给数。
- `cost_override`/`time_override` 用于 Agent 以真实报价/排期手工覆盖，须带 note+evidence。

## 4. 五维评分与淘汰

- 维度：`goal_fit`（目标契合）、`risk`（风险）、`cost`（成本）、`feasibility`（可行性）、`evidence`（证据）；0–5，5 最优；risk/cost 按“越有利分越高”。
- 权重默认 `{goal_fit:.25, feasibility:.25, evidence:.20, cost:.15, risk:.15}`；维度缺失时按可用维度重新归一化。
- 硬阈值：超预算、超时间窗、设备不可得 → 该方案标 `thresholds.feasible=false` 并记原因。
- 敏感性：各权重 ±0.05 扰动重排名，报告 top 方案稳定性分数与逐方案胜出次数。

## 5. 证据溯源（硬要求）

- 每个关键结论/数值/参数范围必须挂 `evidence`，含 `locator`（材料段落定位，如 `materials/paper.md#p3`）+ `quote`（逐字引用）+ `kind`（literature/manual/quote/pilot/log）。
- 冲突信息不隐藏：同一指标两个来源不一致时两个都记录并标 `source_conflict`。
- 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
