# output_contract：plan.json 与 result.json 字段定义

版本化中间表示（IR）的 schema_version = 1。所有路径相对 `/app/output/`（产物）与
`/app/work/`（中间态）。缺值写 `null` 或 `[]`，不删键、不改名。

## 1. plan.json（结构化方案，也是 result.json 的 `plan` 字段）

| 字段 | 类型 | 含义 |
|---|---|---|
| `schema_version` | int | IR 版本，固定 1 |
| `case_id` | str | 取自 `/input/case.json` |
| `status` | str | `ok\|partial\|insufficient_evidence\|input_error` |
| `title` | str | 方案标题 |
| `research_question` | str | 可检验的研究问题 |
| `objective` | str | 优化目标（单目标或多目标+取舍） |
| `assumptions` | list[str] | 假设 |
| `variables` | list | 自变量/因变量/干扰变量 |
| `parameters` | list[object] | 关键参数，见下 |
| `controls` | list | 对照组与固定条件 |
| `procedures` | list | 步骤（可执行、可复核） |
| `resources` | list | 设备/材料/耗材/软件 |
| `response_metrics` | list[object] | 响应指标，见下 |
| `interpretation_criteria` | list | 判读标准（怎么算通过） |
| `search_strategy` | object | `{method, initial_points, per_round, stop_criteria, candidate_count, seed}` |
| `cost_budget` | object | `{items, total, time_hours, tradeoff_note}` |
| `basis` | list | 依据（文献/设备/历史记录） |
| `risks` | list | 风险 |
| `exception_handling` | list | 异常处置 |
| `confirmations` | list | 确认项 |

### 1.1 parameters 元素

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `name` | str | 是 | 参数名（含符号/口径） |
| `unit` | str | 是 | 单位 |
| `role` | str | 否 | `key\|secondary` |
| `suggested_value` | number | 否 | 建议值，须落在可行域 |
| `feasible_range` | object | 否 | `{min, max}` 建议可行浮动区间 |
| `step` | number | 否 | 步进（>0） |
| `limits` | list[object] | 否 | `{kind: device\|material\|safety, min, max, source, quote}` |
| `exclusions` | list | 否 | 互斥/禁止组合 |
| `basis` | object | 是（有数值时） | `{locator, quote, claim?, kind?}` 逐字溯源 |
| `note` | str | 否 | 补充说明 |

### 1.2 response_metrics 元素

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `name` | str | 是 | 指标名 |
| `definition` | str | 否 | 口径定义 |
| `unit` | str | 是 | 单位 |
| `measurement_method` | str | 是 | 测量方法 |
| `direction` | str | 否 | `maximize\|minimize\|target` |
| `target` | number | 否 | 目标值 |
| `acceptable_lower` | number | 否 | 可接受下限 |
| `prediction_method` | str | 否 | 历史拟合/代理模型/经验规则 |
| `uncertainty` | str | 否 | 预测不确定度披露 |
| `basis` | object | 否 | `{locator, quote}` |

## 2. result.json（最终交付）

| 字段 | 类型 | 含义 |
|---|---|---|
| `case_id` | str | 输入标识 |
| `status` | str | 如实状态 |
| `plan` | object | 主结果（= plan.json 内容） |
| `artifacts` | list[object] | `{path, kind, exists}` 实际产物 |
| `sources` | list[object] | `{locator, quote, kind, claims?}` 证据映射 |
| `uncertainty` | object | `{unverified_values, missing_data, conflicts}` |
| `limitations` | list[str] | 限制（如实） |
| `pending_human` | list[str] | 需人工确认项 |
| `change_log` | list[object] | `{ts, step, note}` |
| `errors` | list[str] | 非阻断错误 |
| `degradations` | list[str] | 降级说明 |
| `runtime` | object | `{seconds, requests_estimate, input_digest}` |

## 3. 证据溯源（sources / basis）硬要求

- 每条关键结论/数值都要有 `locator`（如 `materials/paper.md#p3`）+ 逐字 `quote`。
- 冲突信息不隐藏：同一指标两个来源不一致时都记录并标 `kind=source_conflict`。
- 缺 locator 或 quote 的记录由 `core.SourceRegistry` 记为错误；`validate` 默认严格模式（`--strict-sources`）时阻断。
