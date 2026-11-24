# plan_schema_and_evidence：字段语义与证据规范

本文件是 `result.json` 与 `plan.md` 的字段语义、六要素子结构与 `evidence_map`
细粒度结构的权威说明。与 `SKILL.md` 第 3/5 节配套使用。

## 1. result.json 顶层字段

| 字段 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 案例标识；优先 case.json 的 id/case_id/challenge_id，其次 task.md 的 challenge_id，否则 `"unknown"` |
| 契约字段 | * | 题目 `output_contract` 的全部键原样回填（dict 递归、list 变 `[]`、标量变 `null`），不改名、不删未知键 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` |
| `sources` | array | 全部输入来源清单（文件/页码/角色），供复核定位 |
| `uncertainty` | array | 不确定项与约束冲突，含双方事实与影响 |
| `pending_human` | array | 需要安全/伦理/设备/专家确认或人工补齐的缺口 |
| `evidence_map` | object | 细粒度证据映射，见第 4 节 |
| `change_log` | array | 增量变更记录，元素为 `{ts, event, detail}` |
| `artifacts` | object | 主产物路径，如 `{"plan_md": "plan.md"}` |
| `errors` / `degradations` | array | 解析错误 / 降级记录 |

`status` 判定：
- `ok`：六要素全部填写、证据齐全、算术校验通过。
- `partial`：已落盘骨架或部分要素已填，仍在增量改进。
- `insufficient_evidence`：无可用输入材料或材料不足，无法支撑结论。
- `input_error`：输入不可解析且无法继续（仍须写最小合法结果）。

## 2. 六要素子结构（`plan` 内）

`plan` 含六个键；每个键是一个对象，至少包含以下字段（字段缺失不影响结构校验，
但影响「任务完成度」评分）：

- `hypothesis_and_variables`（假设与变量）
  - `research_question`、`hypotheses[]`、`variables[]`
  - 每个变量：`name`、`type`（independent/dependent/confounder）、`operational_definition`、`measurement`、`unit`、`range`、`source`
- `control_groups`（对照组）
  - `groups[]`：`name`、`type`（blank/positive/negative/dose-gradient）、`purpose`、`confounds_excluded[]`、`n`
- `equipment_and_materials`（设备材料）
  - `items[]`：`name`、`model_or_spec`、`quantity`、`unit`、`indispensable`（bool）、`alternative`
- `time_and_cost`（时间成本）
  - `steps[]`（`step`、`duration`、`parallel`、`dependencies[]`）、`total_duration`、`sample_size`、`budget{line_items[],total,currency}`
- `parameter_ranges`（参数范围）
  - `parameters[]`：`name`、`unit`、`min`、`max`、`out_of_range_action`、`basis`（device_manual/preliminary/literature/contract）
- `risks_and_exceptions`（风险与异常）
  - `risks[]`：`failure_mode`、`detection_criterion`、`stop_loss`、`alternative_path`

所有数值必须带单位；所有参数必须写允许区间与超区间处置；所有步骤必须绑定
设备/试剂/时长；所有来源必须落到 locator + quote。

## 3. 依据与风险 / 确认项

- `evidence_and_risks`：`evidence[]`（每条含 `claim`、`locator`、`quote`、`source_role`、`confidence`）与 `risks[]`。
- `confirmations`：`safety[]`、`ethics[]`、`equipment_availability[]`、`expert_review[]`。
  未确认项写 `pending_human` 而**不得**写成可执行事实。

## 4. evidence_map 细粒度结构（本版强化）

每个关键结论一条 claim，使「结论 → 证据 → 引用 → 下游使用位置」可逐条回溯：

```json
"evidence_map": {
  "coverage": {"claims_total": 12, "claims_with_quote": 12},
  "claims": [
    {
      "id": "claim-001",
      "kind": "numerical | methodological | constraint | risk | hypothesis",
      "statement": "结论原文（不带主观推断）",
      "locator": "materials/paper.md#p3 或 case.json#price_table 或 PDF 页码",
      "quote": "材料中的逐字原文",
      "source_role": "paper | contract | device_manual | organizer_input | literature",
      "confidence": "reported | inferred | not_reported",
      "conflict": null,
      "supports": ["plan.parameter_ranges.parameters[0]", "evidence_and_risks.evidence[1]"]
    }
  ]
}
```

硬约束：
- `statement`、`locator`、`quote` 三者非空；`quote` 必须是材料逐字原文，不能改写。
- 两处来源不一致时，保留两条 claim 或在一个 claim 内写 `conflict`（含对方 locator 与 quote），并标 `source_conflict`。
- `confidence=inferred` 只用于能从材料直接推出的结论；材料没有的写 `not_reported` 或移入 `pending_human`。
- 来自「前瞻性合同」的预算/排期/参数，`source_role` 写 `contract`，并在 statement 标注「案例给定约束（非真实报价/审批）」。
- `supports` 指向 result 中的字段路径，证明每条证据被下游方案实际引用。

## 5. fallback 结构（case.json 无 output_contract 时）

```json
{
  "plan": {
    "hypothesis_and_variables": {},
    "control_groups": {},
    "equipment_and_materials": {},
    "time_and_cost": {},
    "parameter_ranges": {},
    "risks_and_exceptions": {}
  },
  "evidence_and_risks": {},
  "confirmations": {}
}
```
