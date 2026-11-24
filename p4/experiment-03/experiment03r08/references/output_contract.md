# 输出契约（output contract）

本文件是 `plan.json`（工作中间态）与 `result.json`（最终交付）的唯一字段口径。
题目没有给出独立 JSON 结构，其“实验方案 / 依据与风险 / 确认项”三类输出要求与
Runtime contract 的 prose 定义以下面字段为准；全部回填，缺值给 `null` 或 `[]`，
不删改未知键、不改键名。

## 1. result.json（最终交付，写入 /app/output/）

| 字段 | 类型 | 含义 / 要求 |
|-|-|-|
| `case_id` | string | 取自 `/input/case.json`，非空 |
| `status` | enum | `ok / partial / insufficient_evidence / input_error`，如实取值 |
| `plan` | object | 主结果，见第 2 节 |
| `artifacts` | object | `{"plan.md": abs, "plan.json": abs, "audit_report.json": abs}`，须真实存在 |
| `contract` | object | `schema_version`、`contract_source`、`fields_covered` |
| `sources` | object[] | 证据来源（`source_id/path/locator/quote/kind/page`） |
| `evidence_map` | object | `claims[]`，见第 3 节 |
| `uncertainty` | object | `unverified_values[]/missing_data[]/assumption_risk[]` |
| `limitations` | string[] | 如实填写；无则 `[]` |
| `pending_human` | string[] | 需专家/安全/设备确认项 |
| `change_log` | object[] | `time/entry`，首条为“骨架落盘” |
| `errors` / `degradations` | object[] | 运行异常；无则 `[]` |
| `runtime` | object | `seconds/started_at/finished_at/model_requests` |
| `rubric_trace` | object[] | 评分维度映射，见第 4 节 |
| `rubric_audit` | object | `pass/warn/fail/na` 计数 + `failures[]`，由 validate_plan.py 计算 |

## 2. plan（主结果，覆盖“实验方案/依据与风险/确认项”）

- `research_question`、`objective`、`title`：可检验的问题与优化目标。
- `assumptions`（string[]）：每条可复核，写明可证伪边界。
- `variables`（object[]）：`name/role/unit/levels_or_range/measured_how`。
- `parameters`（object[]）：**关键参数**，字段见下表，非空。
- `controls`（object[]）：`name/purpose/type`（对照设计）。
- `procedures`（object[]）：`step/action/params/expected/measure`。
- `resources`（object[]）：`item/qty/unit/source`。
- `response_metrics`（object[]）：**响应指标**，字段见下表，非空。
- `interpretation_criteria`（string[]）：判读标准。
- `search_strategy`（object）：`method/initial_points/per_round/stop_criteria/candidate_count/seed`。
- `cost_budget`（object）：`items[]/total/time_hours/tradeoff_note/acceptance_criteria`。
- `basis`（object[]）：`claim/source_id/locator/quote`。
- `risks`（object[]）：`risk/trigger/impact/mitigation`。
- `exception_handling`（object[]）：`scenario/detection/action/decision`，覆盖五类：无可行解、
  约束冲突、数据缺失、设备报错、指标矛盾。
- `confirmations`（object[]）：`item/status/owner`；安全、伦理、设备可用性、专家确认四类。
- `uncertainty`、`limitations`、`pending_human`、`change_log`：同 result.json 顶层含义。

### 关键参数（parameters[]）

`name`、`unit`、`suggested_value`、`feasible_range{min,max}`、`step`、
`equipment_limit{min,max}`、`material_limit{min,max}`、`safety_threshold{min,max}`、
`constraint_source`、`locator`、`quote`（逐字）、`rationale`。

硬要求：`suggested_value` 必须落在 `feasible_range` 内，且不越设备/材料/安全边界；
越界为阻断错误。

### 响应指标（response_metrics[]）

`name`、`unit`、`measurement_method`、`direction`（maximize/minimize/target）、`target`、
`acceptable_lower`、`uncertainty`、`prediction_method`（历史拟合/代理模型/经验规则）、
`prediction_note`。

## 3. evidence_map（每条结论带 locator + 逐字 quote）

```json
{
  "schema_version": "2.0.0",
  "claims": [
    {
      "claim_id": "P1",
      "claim_type": "parameter_value|parameter_domain|constraint|response_metric|predicted_response|cost_basis|risk|assumption|control|resource|procedure|interpretation",
      "statement": "建议温度取 80 °C",
      "status": "supported|inferred|unverified|conflicting|pending_human",
      "confidence": "high|medium|low",
      "provenance": [
        {"source_id": "s1", "locator": "materials/manual.md#L12", "quote": "反应器量程 20–200 °C", "quote_type": "verbatim", "role": "domain_bound|value_support|method|conflict"}
      ],
      "derivation": "由量程上限与耐温 120°C 取 min，初值取 80°C",
      "conflicts": [],
      "verified": true,
      "verification": "script_check|expert_review|none"
    }
  ],
  "conflict_count": 0,
  "unverified_count": 0
}
```

硬要求：`provenance` 至少一条且含 `locator` + `quote`（`quote_type=verbatim`）；来源冲突
双方都记 `conflicting`；未核实结论 `verified=false` 并登记 `pending_human`。

## 4. rubric_trace（评分维度对齐，本版核心）

16 个评分点（R01–R16）逐条映射到证据：`rubric_id`、`dimension`、`weight`、
`target_fields`、`gate`、`evidence`、`notes`。`validate_plan.py` 据此计算
`rubric_audit` 并写 `audit_report.json`。各 R 的权重与门槛见 `references/rubric.md`。
