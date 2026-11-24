# plan_schema_and_evidence：fallback 结构、字段语义与证据规范

本文定义 `result.json` 在没有题目 `output_contract` 时的 fallback 结构、字段语义、status 语义与证据规范。有 `output_contract` 时逐键回填该契约，信封字段与本文语义仍适用。

## 1. fallback 结构（固定 JSON 键序）

```json
{
  "case_id": "string",
  "status": "partial",
  "plan": {
    "hypothesis_and_variables": {
      "hypothesis": "可检验假设（含预期方向）",
      "variables": [
        {"name": "…", "role": "independent|dependent|confounder",
         "operational_definition": "…", "measurement": "…",
         "unit": "…", "range": ["min", "max"]}
      ]
    },
    "controls": [
      {"name": "…", "type": "blank|positive|negative|dose_gradient",
       "purpose": "…", "confounders_excluded": ["…"]}
    ],
    "equipment_and_materials": [
      {"name": "…", "spec": "…", "range_or_grade": "…",
       "substitutable": true, "alternative": "…"}
    ],
    "time_and_cost": {
      "steps": [
        {"step": "…", "duration_min": 0, "dependencies": ["…"],
         "parallelizable": false}
      ],
      "total_duration_min": 0,
      "sample_size": 0,
      "budget": {"amount": 0, "currency": "CNY"},
      "cost_items": [{"item": "…", "amount": 0, "currency": "CNY"}]
    },
    "parameter_ranges": [
      {"name": "…", "unit": "…", "allowed_min": 0, "allowed_max": 0,
       "proposed_value": 0, "source": "manual|preliminary|literature",
       "out_of_range_action": "…"}
    ],
    "risks_and_anomalies": [
      {"failure_mode": "…", "criterion": "…", "stop_point": "…",
       "alternative_path": "…"}
    ]
  },
  "evidence_and_risks": {
    "evidence_map": [
      {"id": "e1", "claim": "…", "locator": "materials/paper.md#p3",
       "quote": "逐字原文", "source_conflict": false}
    ],
    "risks": []
  },
  "confirmations": {
    "safety": [], "ethics": [], "equipment_availability": [], "expert_review": []
  },
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "errors": [],
  "degradations": [],
  "change_log": [{"ts": "ISO8601 UTC", "event": "skeleton_written", "detail": "…"}],
  "runtime": {}
}
```

## 2. 信封字段语义

- `case_id`：输入标识；缺失写 `"unknown"` 并同时列 `pending_human`。
- `status`：`ok | partial | insufficient_evidence | input_error`。语义：
  - `ok`：六要素齐全、证据可回溯、算术自洽，无未解决缺口。
  - `partial`：主方案可用但有未解决缺口/待确认项。
  - `insufficient_evidence`：材料不足，无法形成可执行方案。
  - `input_error`：输入无法解析（坏 JSON/缺 case.json）。
- `sources`：`[{id, path_or_uri, type, note}]`，只记真实读到的来源。
- `uncertainty`：`[{id, issue, options, needs_expert}]`，冲突或不确定必写。
- `pending_human`：需人确认的事项清单（安全、伦理、设备、价格、审批）。
- `change_log`：`[{ts, event, detail}]`，每次落盘追加，`event` 用 snake_case。
- `errors`/`degradations`/`runtime`：异常、降级与耗时记录（可选，但建议写）。

## 3. 六要素操作化要求

- **假设与变量**：假设须可证伪、含方向；每个变量有操作定义、测量方式、单位、量程；标注 independent/dependent/confounder。
- **对照组**：类型用 blank/positive/negative/dose_gradient；每个对照说明排除哪类混杂。
- **设备材料**：绑定型号/量程/规格/试剂；标可替代与不可替代。
- **时间成本**：步骤时长、依赖与并行关系、样本量、预算；可排期可报价。
- **参数范围**：`allowed_min/max`、`proposed_value`、单位、依据来源（manual/preliminary/literature）、越界处置。
- **风险与异常**：失败模式、判据、止损点、替代路径。

## 4. 证据规范

- 每条 `evidence_map` 项含 `locator` + `quote`（逐字原文，不转述）。
- `locator` 形式：`materials/paper.md#p3`、`materials/foo.pdf#p4`、`case.json#budget`。
- 两处来源不一致：两条都记录，并标 `source_conflict: true`。
- 不虚构 DOI/引用/数值；缺失写 `not_reported` 或 `pending_human`。

## 5. 算术自检口径（validate_result.py 强制项）

- 参数：`proposed_value ∈ [allowed_min, allowed_max]`，且 `allowed_min ≤ allowed_max`。
- 预算：`sum(cost_items.amount) ≤ budget.amount`。
- 排期：`total_duration_min ≥ max(step.duration_min)`；若步骤标为并行，允许 `total < sum`，否则给出提示。
- 样本可行性：`sample_size`、单样本时长与时间窗同时存在时，检查 `sample_size × 单样本时长 ≤ 时间窗`，不满足写入 `uncertainty` 而非静默。
