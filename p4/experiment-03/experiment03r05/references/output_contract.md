# 输出契约：result.json 与 plan.json 字段定义

本文件是 `scripts/validate.py` 强制执行的字段契约，也是 `result.json` 逐一对齐的依据。字段「必填」= 校验器按缺失记为阻断错误；「建议」= 缺失记为警告（材料不足时允许为 null/[] 并在 `limitations` 说明）。

## 1. result.json 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|-|-|-|-|
| `case_id` | string | 是 | 取自 `/input/case.json`，非空；须与 `plan.case_id` 一致 |
| `status` | enum | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error`，如实反映完成度 |
| `plan` | object | 是 | 主结果，结构见第 3 节 |
| `artifacts` | list | 是 | 可编辑产物清单，每项 `{path, kind, role}`，path 指向真实存在的文件 |
| `sources` | list | 是 | 证据映射，每项 `{id, locator, quote, supports[], status}` |
| `uncertainty` | object | 是 | `{unverified_values[], missing_data[], assumptions[], prediction_notes}` |
| `limitations` | list | 是 | 如实记录方法/材料/工具限制，可为字符串或对象 |
| `pending_human` | list | 是 | 待人工确认项 `{id, item, kind, why}` |
| `change_log` | list | 是 | 审计轨迹，非空；首条为「骨架落盘」 |
| `errors` | list | 是 | 运行期可恢复错误（坏 JSON、坏编码等），无则 `[]` |
| `degradations` | list | 是 | 降级措施记录，无则 `[]` |
| `runtime` | object | 是 | `{seconds, model_requests, notes}` |

`status` 语义：
- `ok`：关键参数/响应/搜索/成本四项齐全，可行域内校验通过，无未解决阻断。
- `partial`：已落盘骨架，四项未全部核完，但字段结构合法。
- `insufficient_evidence`：材料不足以支撑参数/响应/约束的确定值，已写入 uncertainty/limitations，未编造。
- `input_error`：输入全部不可读或 case_id 缺失且无法回填。

若 `/input/case.json` 自带 `output_contract`，其全部键原样回填（未知键保留，缺值 `null`），不删不改名。

## 2. change_log 条目

每条必须为对象：`{time, step, action, detail, content_hash}`。
- `time`：ISO-8601 UTC，如 `2026-11-23T08:00:00Z`。
- `step`：`skeleton` / `modeling` / `cost_risk` / `finalize` / `degrade`。
- `action`：动词短语，如 `committed_partial_skeleton`。
- `detail`：中文说明改了什么。
- `content_hash`：当时 plan.json 的 sha256 前 16 位（规范化 JSON，`sort_keys=True`），保证可复现。

首条固定为骨架落盘；`finalize` 会追加一条并继承既有日志，不覆盖审计轨迹。

## 3. plan 对象字段

`plan` 必须包含以下键（即使为空也须存在）：

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 与顶层一致 |
| `title` | string | 方案标题 |
| `research_question` | string | 可检验的问题 |
| `objective` | string | 目标（含方向） |
| `assumptions` | list | 假设 `{id, text, evidence}` |
| `variables` | list | 变量 `{id, name, type, unit, role, levels?}` |
| `parameters` | list | 关键参数，见 3.1 |
| `controls` | list | 对照/固定条件 |
| `procedures` | list | 步骤 `{id, step, note?}` |
| `resources` | list | 设备/材料 `{id, name, spec?}` |
| `response_metrics` | list | 响应指标，见 3.2 |
| `interpretation_criteria` | list | 判读标准 |
| `search_strategy` | object | 见 3.3 |
| `cost_budget` | object | 见 3.4 |
| `basis` | list | 依据映射 `{id, claim, source}` |
| `risks` | list | 风险 `{id, risk, likelihood?, impact?, mitigation?}` |
| `exception_handling` | list | 异常处置 `{id, condition, action}` |
| `confirmations` | list | 确认项 `{id, item, kind, status}` |

### 3.1 关键参数（parameters[]）

必填：`name`、`unit`、`suggested_value`（数值或 null）、`feasible_range{min,max}`（min≤max）、`step`（>0）、`constraint_source`、`constraint_quote`（逐字）、`bounds_origin`。

校验规则：
- `suggested_value` 为数值时必须落在 `[min, max]` 内，越界 = 阻断错误。
- `step` 必须为正数；`min ≤ max`。
- `bounds_origin` 取值：`device_range` / `material_limit` / `safety_threshold` / `historical` / `expert` / `unspecified`。
- 建议补充 `interactions`（与其他参数的互斥/耦合）与 `id`。

### 3.2 响应指标（response_metrics[]）

必填：`name`、`definition`、`unit`、`measurement_method`、`direction`（`maximize`/`minimize`/`target`）。

按方向补边界：`maximize` → `acceptable_lower_bound`；`minimize` → `acceptable_upper_bound`；`target` → `target`。同时补 `prediction_method`（`historical_fit`/`surrogate`/`empirical_rule`/`none`）与 `prediction_uncertainty`（预测不确定度的具体量或说明）。

### 3.3 搜索策略（search_strategy）

| 字段 | 类型 | 说明 |
|-|-|-|
| `method` | enum | `grid` / `sequential` / `bayesian` / `staged_screen` / `custom` |
| `initial_points` | list | 初始点 `{params, reason?, round?}` |
| `per_round` | string | 每轮补哪些点 |
| `convergence_criteria` | list | 收敛判据 |
| `stop_criteria` | list | 停止判据（非空，阻断校验） |
| `candidate_count` | int | 候选点总数 ≥1 |
| `seed` | int | 固定随机种子，保证复现 |
| `rounds_planned` | int | 计划轮数 |

`main.py doe` 先生成 initial_points，再用 `random.Random(seed)` 确定性补网格点至 `candidate_count`。

### 3.4 成本预算（cost_budget）

| 字段 | 类型 | 说明 |
|-|-|-|
| `items` | list | 明细 `{item, amount, unit, rate?, cost}` |
| `total` | string | 总成本与口径 |
| `time_hours` | number/null | 总工时 |
| `sample_count` | number/null | 样品/机时数 |
| `throughput_note` | string | 通量说明 |
| `tradeoff_note` | string | 多目标取舍：牺牲多少响应指标可接受 |

## 4. 证据溯源（sources[]）

每项 `{id, locator, quote, supports[], status}`。
- `locator`：材料内定位，如 `materials/paper.md#p3` 或 `case.json#constraints.temperature_max`。
- `quote`：逐字引用（不得改写、不得概括）。
- `supports`：该证据支撑的字段路径，如 `["plan.parameters[0]", "plan.basis[0]"]`。
- `status`：`verified` / `unverified` / `source_conflict`；冲突时两个来源都保留并标 `source_conflict`。

关键数值/约束必须可指回 quote；缺失写 `pending_human` 或 `not_reported`，不得虚构 locator/quote/DOI。

## 5. 校验器退出码

`scripts/validate.py plan|result ...`：
- 0 = 通过（无阻断、无警告）
- 1 = 存在阻断错误（必须修复或明确降级）
- 2 = 仅警告（允许交付，警告须在 limitations 说明）

`scripts/selftest.py` 退出码 0 = 自检通过；非 0 = 自检失败。
