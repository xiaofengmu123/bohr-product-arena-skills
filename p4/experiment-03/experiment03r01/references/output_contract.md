# 输出契约（output contract）

本文定义 `/app/work/plan.json` 与 `/app/output/result.json` 的全部字段。
字段命名以下文为准；题目给出的“实验方案 / 依据与风险 / 确认项”三类输出要求
必须全部回填到对应字段，缺值给 `null` 或 `[]`，不删改未知键。

## 1. plan.json（工作目录中间态，finalize 后合并进 result.json 的 `plan`）

| 字段 | 类型 | 含义 | 要求 |
|-|-|-|-|
| `case_id` | string | 取自 `/input/case.json` | 非空 |
| `status` | enum | `ok / partial / insufficient_evidence / input_error` | 骨架阶段为 `partial` |
| `title` | string | 方案标题 | 可空 |
| `research_question` | string | 可检验的研究问题 | 建议非空 |
| `objective` | string | 优化目标 | 建议非空 |
| `assumptions` | string[] | 假设 | 每条可复核 |
| `variables` | object[] | 变量（`name/role/unit/levels_or_range/measured_how`） | — |
| `parameters` | object[] | **关键参数**，见下表 | 非空 |
| `controls` | object[] | 对照（`name/purpose/type`） | — |
| `procedures` | object[] | 步骤（`step/action/params/expected/measure`） | — |
| `resources` | object[] | 资源（`item/qty/unit/source`） | — |
| `response_metrics` | object[] | **响应指标**，见下表 | 非空 |
| `interpretation_criteria` | string[] | 判读标准 | 建议非空 |
| `search_strategy` | object | **搜索策略**：`method/initial_points/per_round/stop_criteria/candidate_count/seed` | 必填 |
| `cost_budget` | object | **成本预算**：`items[]/total/time_hours/tradeoff_note` | 必填 |
| `basis` | object[] | 依据（`claim/source_id/locator/quote`） | 逐字溯源 |
| `risks` | object[] | 风险（`risk/trigger/impact/mitigation`） | — |
| `exception_handling` | object[] | 异常处置（`scenario/detection/action/decision`） | 覆盖五类场景 |
| `confirmations` | object[] | 确认项（`item/status/owner`）；未确认写入 `pending_human` | — |
| `sources` | object[] | 来源清单（`source_id/path/locator/quote/kind/page`） | — |
| `claims` | object[] | **证据溯源 claims**，见第 3 节 | 每条带 locator+quote |
| `uncertainty` | object | `unverified_values[]/missing_data[]/assumption_risk[]` | — |
| `limitations` | string[] | 如实际限制 | 如实填写 |
| `pending_human` | string[] | 需专家确认项 | 未确认不写成事实 |
| `change_log` | object[] | `time/entry` | 首条“骨架落盘” |

### 关键参数（parameters[]）字段

`name`（名称）、`unit`（单位）、`suggested_value`（建议值）、
`feasible_range{min,max}`（可行区间）、`step`（步进）、
`equipment_limit{min,max}`（设备量程）、`material_limit{min,max}`（材料耐温）、
`safety_threshold{min,max}`（安全阈值）、`constraint_source`（约束来源 source_id）、
`locator`（定位）、`quote`（逐字原文）、`rationale`（取舍理由）。

建议值必须落在 `feasible_range` 内，且不越设备/材料/安全边界。

### 响应指标（response_metrics[]）字段

`name`、`unit`、`measurement_method`（测量方法）、`direction`（maximize/minimize/target）、
`target`、`acceptable_lower`（可接受下限）、`uncertainty`（不确定度）、
`prediction_method`（历史拟合/代理模型/经验规则）、`prediction_note`（预测披露）。

## 2. result.json（最终交付）

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| `status` | 如实取四枚举之一 |
| `plan` | 主结果，等于 plan.json 内容键合并（含 case_id/status） |
| `artifacts` | `{"plan.md": ..., "plan.json": ...}` 绝对路径，须真实存在 |
| `sources` | 证据来源清单 |
| `evidence_map` | 见第 3 节 |
| `uncertainty` / `limitations` / `pending_human` / `change_log` | 与 plan 一致 |
| `errors` / `degradations` | 运行异常记录，无则 `[]` |
| `runtime` | `seconds/started_at/finished_at/model_requests` |

## 3. evidence_map（证据溯源强化：每条结论带 locator + 逐字 quote）

```json
{
  "schema_version": "2.0.0",
  "claims": [
    {
      "claim_id": "P1",
      "claim_type": "parameter_value | parameter_domain | constraint | response_metric | predicted_response | cost_basis | risk | assumption | control | resource | procedure | interpretation",
      "statement": "建议温度取 80 °C",
      "status": "supported | inferred | unverified | conflicting | pending_human",
      "confidence": "high | medium | low",
      "provenance": [
        {
          "source_id": "s1",
          "locator": "materials/manual.md#L12",
          "quote": "反应器量程 20–200 °C",
          "quote_type": "verbatim",
          "role": "domain_bound | value_support | method | conflict"
        }
      ],
      "derivation": "由量程上限与耐温 120°C 取 min，取初值 80°C",
      "conflicts": [],
      "verified": true,
      "verification": "script_check | expert_review | none"
    }
  ],
  "conflict_count": 0,
  "unverified_count": 0
}
```

硬性要求：

- `provenance` 至少一条，且每条含 `locator` + `quote`（`quote_type` 恒为 `verbatim`，
  不得用改写代替逐字引用）。
- 来源冲突不隐藏：同一指标多来源不一致时，双方都记入 `provenance` 且
  `role` 标 `conflict`，`status` 标 `conflicting`，计入 `conflict_count`。
- 未核实结论：`verified=false`、`status=unverified`，并在 `pending_human` 中登记。
- 每条关键参数、每条响应指标、每条依据、每条风险，都应对应至少一条 claim。
