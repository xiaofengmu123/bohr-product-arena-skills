# result.json 输出契约精确定义

本文定义 `result.json` 的字段语义、取值与证据要求。脚本 `scripts/main.py` 的 `skeleton` 按此生成骨架，`validate` 按此校验，`report` 按此渲染 `report.md`。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 来自 `/input/case.json` 或 `task.md` 的 challenge_id；不得为空 |
| `status` | string | 是 | `ok` \| `partial` \| `insufficient_evidence` \| `input_error` |
| `task_type` | string | 是 | 默认 `experiment_result_interpretation` |
| `summary` | string \| null | 是 | 1–3 句平实结论，无数据时写 `null` |
| `interpretation` | object | 是 | 五段结论，见下 |
| `experiment_plan` | object | 是 | 实验方案，见下 |
| `evidence_and_risk` | object | 是 | 依据与风险，见下 |
| `confirmation_items` | object | 是 | 确认项，见下 |
| `sources` | array | 是 | 材料来源清单（路径/版本/摘要） |
| `uncertainty` | array | 是 | 单位、方法、阈值等歧义记录 |
| `change_log` | array | 是 | 阶段记录：`stage`/`ts`/`note` |
| `errors` | array | 是 | 坏 JSON、读写失败等错误 |
| `degradations` | array | 是 | 坏编码、超限、缺工具等降级记录 |
| `limitations` | array | 是 | 对照缺失、重复数不足等限制 |
| `pending_human` | array | 是 | 未经专家确认、不能写成事实的事项 |
| `runtime` | object | 是 | `started_at`/`finished_at`/`duration_s` |

未知键：不改名、不删除；若 `case.json` 含 `output_contract`，其全部键原样回填（缺值 `null`/`[]`）。

## 2. `interpretation`（五段结论）

每段为数组，元素为对象；**每条对象必须带 `evidence` 数组**，每项含 `locator` + `quote`。

- `trends`：`direction`（增/减/稳定/非单调/拐点/平台期）、`interval`、`control`、`fit_method`、`magnitude`、`unit`
- `anomalies`：`object`（样本/批次/通道/孔位/时间点）、`observed`、`expected`、`deviation`、`threshold_method`、`threshold_value`、`exceeds_threshold`、`relation_to_control`
- `possible_causes`：`cause`、`rank`、`supporting_evidence`（数组，每项 locator+quote）、`excluding_evidence`、`confound_risk`、`testability`
- `verification_steps`：`target`、`expected_if_supported`、`decision_rule`（第二人可执行）
- `followup_experiments`：`change`、`reason`、`expected_outcome`

## 3. `experiment_plan`

- `hypothesis`：string \| null
- `testable_question`：string \| null
- `variables`：array（自变量/因变量/混杂变量）
- `controls`：array
- `steps`：array
- `parameters`：array（参数范围与单位）
- `resources`：array（设备/材料/时间成本）
- `interpretation_criteria`：string \| null（判读标准）

## 4. `evidence_and_risk` / `confirmation_items`

- `evidence_basis`：string \| null（文献/设备/历史记录依据）
- `risk_and_anomaly_handling`：string \| null（风险与异常处置）
- `assumptions`：string \| null（假设）
- `safety` / `ethics` / `equipment_availability` / `expert_confirmation`：string \| null

## 5. 证据条目

```json
{"locator": "data/batch_12.csv#L34", "quote": "force=9.9"}
```

- `locator`：文件相对 `/input` 的路径 + 定位符（行号/段落/sheet/字段），如 `materials/paper.md#p3`。
- `quote`：材料中的**逐字摘录**，不得改写或补造。
- 同一指标两来源冲突：两条都记录并标 `source_conflict: true`。

## 6. status 语义

| status | 含义 |
|---|---|
| `ok` | 材料充足，五段结论已完整填写并附证据 |
| `partial` | 已产出部分结论，仍有缺口（在 limitations/pending_human 说明） |
| `insufficient_evidence` | 无数据/空输入，只写缺口不填充示例 |
| `input_error` | 输入损坏（坏 JSON 等）导致无法解析关键元数据 |
