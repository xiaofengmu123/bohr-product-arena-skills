# 输出契约（plan.json / result.json 字段定义）

本文是 `scripts/main.py`、`scripts/validate_plan.py`、`scripts/check_result` 校验所依据的字段规范。
所有字符串为 UTF-8；未知键不删除、不改名；缺失值给 `null` / `[]` / `""`，不要编造。

## 1. 顶层 result.json（`/app/output/result.json`）

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 取自 `/input/case.json`，找不到时给 `""` 并在 `pending_human` 说明 |
| `status` | enum | 是 | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `title` | string | 否 | 方案标题 |
| `plan` | object | 是 | 主结果，含下述 16 个方案内容键 |
| `artifacts` | array | 是 | 可编辑产物清单，每项 `{path, kind, exists}`；`path` 相对 `/app/output/` |
| `sources` | array | 是 | 证据映射，每项至少含 `locator` 与逐字 `quote` |
| `uncertainty` | object | 是 | 见 §3 |
| `limitations` | array | 是 | 能力与材料限制，逐条如实说明 |
| `pending_human` | array | 是 | 未经确认、须专家/安全/伦理确认的项 |
| `change_log` | array | 是 | 增量修改记录，每项 `{at, entry}` |
| `errors` | array | 是 | 运行中捕获的错误 |
| `degradations` | array | 是 | 降级说明（缺软件、缺材料、坏编码等） |
| `runtime` | object | 是 | `{started_at, finished_at, seconds, model_requests}` |

## 2. plan 内容键（`result.json["plan"]` 与 `/app/output/plan.json`）

| 键 | 类型 | 内容要求 |
|---|---|---|
| `research_question` | string | 可检验的问题 |
| `objective` | string | 研究目标 |
| `assumptions` | array | 假设，逐条可标注 `evidence_level` |
| `variables` | array | 自变量/控制变量列表 |
| `parameters` | array | 关键参数，见 §4 |
| `controls` | array | 对照设计 |
| `procedures` | array | 步骤，可执行粒度 |
| `resources` | array | 设备/材料/试剂 |
| `response_metrics` | array | 响应指标，见 §5 |
| `interpretation_criteria` | array | 判读标准 |
| `search_strategy` | object | `{method, initial_points, per_round, stop_criteria, candidate_count, seed}` |
| `cost_budget` | object | `{items, total, time_hours, tradeoff_note}` |
| `basis` | array | 依据（文献/设备/历史），每项含 `locator`+`quote` |
| `risks` | array | 风险清单 |
| `exception_handling` | array | 异常处置（无可行解/约束冲突/数据缺失/设备报错/指标矛盾） |
| `confirmations` | array | 安全/伦理/设备可用性/专家确认项 |

## 3. uncertainty 对象

`unverified_values`（未验证数值）、`missing_data`（缺失数据）、`assumptions`（假设）、
`model_notes`（预测方式：历史拟合/代理模型/经验规则，并披露不确定度）。
禁止把未验证数值写成可执行事实；不能确定的写入上述字段并在 `pending_human` 中转人工。

## 4. 参数对象（`parameters[]`）

| 字段 | 必需 | 说明 |
|---|---|---|
| `name` | 是 | 参数名 |
| `unit` | 是 | 单位 |
| `suggested_value` | 是 | 建议值（数值） |
| `min` / `max` | 是 | 可行区间（数值） |
| `step` | 否 | 搜索步进 |
| `device_limit` / `material_limit` / `safety_limit` | 否 | 硬约束 `{min, max}`，用于越界校验 |
| `constraint_source` | 推荐 | 约束来源 locator |
| `quote` | 推荐 | 逐字引用片段 |
| `evidence_level` | 推荐 | `quoted` / `derived` / `assumed` / `pending` |

**可行域校验规则（`validate_plan.py` 阻断错误）**：`min ≤ suggested_value ≤ max`；
`min`/`max` 不得越出 `device_limit` / `material_limit` / `safety_limit`；建议值不得越出三类硬约束。
无逐字来源且未标 `assumed`/`pending` 的数值记警告。

## 5. 响应指标对象（`response_metrics[]`）

`name`、`unit`、`method`（测量方法）、`direction`（`maximize`/`minimize`/`target`）、
`target`（目标）、`acceptable_lower`（可接受下限）、`constraint_source`、`quote`、`evidence_level`。

## 6. 产物文件

`/app/output/plan.md`（可打开可编辑）、`/app/output/plan.json`（结构化规格）、
`/app/output/result.json`（交付结果）。三者路径互相对应且真实存在；`check` 会校验存在性。
