# 输出契约、字段语义与证据规范

本文件定义 `result.json` 的结构、六要素的字段语义、证据台账（claim_id）与算术校验规范。运行时优先回填 `case.json` 的 `output_contract` 全部键；无契约时使用下方 fallback 结构。

## 1. result.json 顶层

| 键 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 取 case.json 的 case_id/id/challenge_id，否则 task.md 的 challenge_id，仍无写 `"unknown"` 并记 pending_human |
| `[output_contract 全部键]` | 原样 | 逐键原样回填，缺值 `null`/`[]`，不改名、不删未知键 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error`，必须诚实 |
| `artifacts` | list | 主产物清单，`{path, role, format}` |
| `sources` | list | 输入来源，`{id, locator, kind}` |
| `uncertainty` | list | 不确定项，`{item, detail, alternatives, needs_expert}` |
| `pending_human` | list | 待人工确认，`{item, why, options}` |
| `change_log` | list | `{ts, event, detail}`，每次大改动原子写回 |
| `errors` / `degradations` / `runtime` | 可选 | 失败记录 / 降级记录 / 运行元信息 |

fallback 契约（无 `output_contract` 时）：

```json
{
  "plan": {
    "hypotheses_and_variables": {}, "controls": {}, "equipment_materials": {},
    "time_cost": {}, "parameter_ranges": {}, "risks_anomalies": {}
  },
  "evidence_and_risks": {"evidence_ledger": {}, "risk_register": [], "arithmetic_checks": []},
  "confirmations": {"safety": [], "ethics": [], "equipment_availability": [], "expert_review": []}
}
```

## 2. 六要素字段语义（全部落到操作定义 / 单位 / 量程 / 判据）

### 假设与变量 hypotheses_and_variables
- `research_question`：可检验问题（不是复述目标）。
- `hypotheses`：`{id, statement, falsifiable_criterion, claim_ids[]}`；每条假设给出可证伪判据。
- `variables`：`{id, name, role(independent|dependent|confounder), operational_definition, measurement_method, unit, expected_range, source}`。
- 判据：假设可检验；变量与测量方式匹配；混杂变量必须单独列出。

### 对照组 controls
- `controls`：`{id, type(blank|positive|negative|dose_gradient|sham|untreated), purpose(排除哪类混杂), expected_outcome, claim_ids[]}`。
- 判据：每个对照写明排除的混杂；至少覆盖与目标匹配的对照，不得只有空白组。

### 设备材料 equipment_materials
- `items`：`{id, name, model_spec, range_or_spec, purpose, substitutable(bool), substitute, claim_ids[]}`。
- 判据：步骤绑定到型号/量程/耗材规格/试剂；标出可替代与不可替代项。

### 时间成本 time_cost
- `steps`：`{id, name, duration_h, parallel_with[], resources[], claim_ids[]}`。
- `total_time_h`、`sample_size`、`budget_estimate`、`budget_currency`、`schedule_notes`。
- 判据：时长、并行关系、样本量、预算可排期可报价；total_time 与各串行步骤之和自洽。

### 参数范围 parameter_ranges
- `parameters`：`{id, name, unit, allowed_min, allowed_max, basis(device_manual|preliminary|literature|contract), out_of_range_action, claim_ids[]}`。
- 判据：参数在设备/试剂量程内；超区间处置明确；依据来源标注。

### 风险与异常 risks_anomalies
- `risks`：`{id, failure_mode(污染|无信号|重复性差|样本不足|超预算|超排期), detection_criterion, stop_loss_point, alternative_path}`。
- 判据：每个风险有失败模式、判据、止损点、替代路径。

## 3. 证据台账 evidence_ledger（claim_id → 溯源）

每个关键结论/数值只记一次，六要素用 `claim_ids[]` 引用：

```json
{"C1": {"text": "结论表述", "locator": "materials/paper.pdf#p3", "quote": "逐字原句",
        "status": "confirmed|source_conflict|inferred|not_reported", "conflict_with": []}}
```

规则：
- `quote` 必须是从材料逐字复制，不得改写或补写。
- 同一指标两处来源不一致：两条都记，标 `source_conflict`，`conflict_with` 互指，并写进 `uncertainty`。
- 缺失写 `not_reported` 并列入 `pending_human`；不虚构引用/DOI/数值/价格/排期。
- 组织方前瞻性资源/价格/排期合同只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 4. 算术校验 arithmetic_checks

对候选方案的量程/预算/排期做显式校验，每项记录：

```json
{"id": "A1", "statement": "总价=单价×份数", "left": 5000, "right": 4900,
 "relation": "sum_eq|eq|le|lt|ge", "expected": "5000 == 4900", "result": "pass|fail|needs_human", "claim_ids": []}
```

`validate` 会对数值型 `left/right/relation` 做确定性复核；无法机械判断的写 `needs_human` 并列入 `pending_human`。

## 5. plan.md

人类可读完整方案：六要素 + 依据与风险 + 确认项；与 result.json 同源一致；case_id 必须出现。最终状态六要素均非空。
