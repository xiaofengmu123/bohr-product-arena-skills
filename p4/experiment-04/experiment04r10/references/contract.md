# 契约与口径规范（contract）

本文定义脚本与 Agent 之间的字段契约、证据规则与成本/时间口径，供独立复跑与复核。

## A. result.json 信封（envelope）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 案例标识，从 case.json/task.md 提取，未知时 `unknown` |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `research_question` | object | testable_question + variables{independent,dependent,controlled} + hypothesis |
| `comparison_frame` | object | objectives[] + hard_constraints[] + criteria[] |
| `candidates` | array | 每个候选方案统一七类字段 + 成本/时间/可行性/风险/评分 |
| `comparison_matrix` | object | 字段×方案矩阵 cells、scores、normalized |
| `selection` | object | chosen/ranking/rationale/rejected[]/sensitivity |
| `confirmations` | array | 安全/伦理/设备/专家确认项 |
| `sources` | array | {id, claim, locator, quote, kind, confidence} |
| `uncertainty` | array | {item, level, note} |
| `change_log` | array | {time, step, note}，增量记录 |
| `pending_human` | array | 需人确认的事项 |
| `errors` / `degradations` | array | 运行错误与降级记录 |
| `runtime` | object | started_at/assemble_at/validate_at |
| `artifacts` | array | {path, kind, note} |
| `limitations` | array | 已知限制 |

`output_contract` 的字段**原样回填为 result.json 顶层键**；无显式契约时回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组。

## B. output_contract 字段映射

- `experimental_plan`：{testable_question, variables, candidates[]}，每个 candidate 含七类字段 + 参数来源。
- `evidence_and_risks`：{evidence[]（台账条目）, risks[]（逐方案，含 trigger/consequence/magnitude/mitigation/fallback）, exceptions[]（异常处置）}。
- `confirmation_items`：confirmations 数组（type ∈ safety/ethics/equipment/expert）。

## C. 七类字段（每方案必填）

assumptions（假设）、variables（变量）、controls（对照）、steps（步骤）、parameters（参数）、resources（资源）、interpretation_criteria（判读标准）。

## D. 五维评分（0–5，越高越好）

goal_fit（目标契合）、risk（风险，5=低风险）、cost（成本，5=低成本）、feasibility（可行性）、evidence（证据强度）。权重默认 0.25/0.15/0.15/0.25/0.20；缺失维度由脚本重新归一化。

## E. 成本与时间口径

- 成本六项：机时 + 耗材 + 人力 + 样品准备 + 排期 + 返工；`per` ∈ `once`（一次性）/`run`（按次），脚本算 one_time 与 per_run。
- 时间五项：prep（准备）+ scheduling（排期）+ execution（执行）+ characterization（表征）+ rework（返工）。
- `method` ∈ `computed`（脚本按 lines/parts 求和）/`override`（Agent 核对后覆盖 total）/`not_computable`（无法核算，total=null，转 pending_human）。

## F. 证据规则

- 每个关键结论/数值/参数范围挂一条台账（locator + 逐字 quote + kind + confidence）。
- locator 格式：`materials/xxx.md#L12`（行号）、`materials/xxx.pdf#p3`（页号，经 pdf 工具提取后）。
- 来源冲突：两个来源不一致时都记录，`conflicts_with` 互指，并在 uncertainty 标注。
- 缺失：写 pending_human 或 not_reported，禁止编造 DOI、数值、引用。

## G. 硬阈值与敏感性

- hard_constraints 支持 name ∈ budget/cost、time/duration/deadline、equipment_available、lead_time；op ∈ <=、<、>=、>、==、!=；违反者进 infeasible_reasons，不参与正常排名或标注不可行。
- 敏感性：每个维度权重 ±0.05（截断 ≥0 后重归一化），重算排名，记录 chosen 保持第一的次数与扰动后仍成立的结论。
