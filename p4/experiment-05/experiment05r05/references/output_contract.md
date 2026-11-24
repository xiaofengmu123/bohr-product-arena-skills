# 输出契约（result.json）字段定义

本文件是 `result.json` 的权威字段定义。`scripts/main.py` 中的契约常量与本文件保持一致；`validate` 按此逐键校验。规则：未知值写 `null` 或 `[]`，不改名、不删键、保留 case.json `output_contract` 中出现的未知键。

## 1. 顶层字段（16 个，必填）

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 来自 case.json（`case_id`/`id`/`challenge_id`/`armiId`）或 task.md 的 challenge_id；两者都无时用输入目录名兜底。非空。 |
| `status` | enum | `ok`｜`partial`｜`insufficient_evidence`｜`input_error`。必须诚实：材料不足给 `insufficient_evidence`。 |
| `task_type` | string | 固定 `experiment_result_interpretation`。 |
| `summary` | string | 1–3 句平实结论，不含未证实机制。 |
| `interpretation` | object | 五段结论，见第 2 节。 |
| `experiment_plan` | object | 实验方案，见第 3 节。 |
| `evidence_and_risk` | object | 依据与风险，见第 4 节。 |
| `confirmation_items` | object | 确认项，见第 5 节。 |
| `sources` | list | 来源清单，每项建议 `{locator, kind, note}`。 |
| `uncertainty` | list | 不确定性，每项建议 `{topic, note, affects}`；单位/方法/阈值歧义必须写在这里，不擅自统一。 |
| `change_log` | list | 阶段留痕，每项 `{ts, phase, action, scope[], summary}`；phase 建议 `P0-skeleton/P1-profile/P2-interpretation/P3-finalize`。 |
| `errors` | list | 运行错误，每项 `{locator, quote, note}`。 |
| `degradations` | list | 降级处理，每项 `{rel/kind, reason}`；坏编码、超 2MB、二进制未导出等写这里。 |
| `limitations` | list | 材料/方法限制：对照缺失、重复数不足、超量程、饱和、样本量小等，每项 `{locator, quote, note}`。 |
| `pending_human` | list | 未经专家确认或缺失的事项，每项 `{topic, note, decision_needed}`。 |
| `runtime` | object | `{started_at, finished_at, elapsed_seconds, budget_seconds=900, budget_requests=64}`。 |

## 2. `interpretation`（五个列表）

每个列表元素必须是 object，且**必须**带 `evidence` 非空数组；每项证据为 `{locator, quote}`，两者都必须是非空字符串（`quote` 为逐字摘录，`locator` 指向文件/字段/行）。

- `trends`：`id, claim, direction, interval, control, fit_method, magnitude, unit, evidence`
  - `direction` ∈ 增/减/稳定/非单调/有拐点；必须写方向与拐点，禁止只复述读数。
- `anomalies`：`id, claim, object, observed, expected, deviation, threshold_method, threshold_value, exceeds, relation_to_control, evidence`
  - `threshold_method` ∈ z-score/IQR/MAD/Grubbs/手动；`threshold_value` 写具体数值；`object` 定位到样本/批次/通道/孔位/时间点。
- `possible_causes`：`id, claim, rank, confidence, supporting_evidence[], excluding_evidence[], confound_risk, testability, evidence`
  - 按证据强度/可检验性排序；`supporting_evidence` 与 `excluding_evidence` 为字符串数组；机制猜测无排除过程时 `confidence` 只能为低，并写缺哪步验证。
- `verification_steps`：`id, claim, action, target, expected_if_supported, decision_rule, evidence`
  - `action` 是第二个人可直接执行的核查动作；`target` 写具体文件/字段/行/sheet 或加做对照；禁止“检查数据质量”这类空话。
- `followup_experiments`：`id, claim, change, reason, expected_outcome, evidence`
  - `change` 写具体改动（补对照/加密采样/调参数范围/扩重复数）。

## 3. `experiment_plan`（8 个子键）

`hypothesis`(str/null)、`testable_question`(str/null)、`variables`(list)、`controls`(list)、`steps`(list)、`parameters`(list)、`resources`(list)、`interpretation_criteria`(str/null)。完成标准：实验人员可据此执行或明确知道缺什么。

## 4. `evidence_and_risk`（3 个子键）

`evidence_basis`(list)：文献/设备/历史记录依据，每项 `{locator, quote, note}`；`risk_and_anomaly_handling`(str/null)；`assumptions`(list)。完成标准：假设和边界可复核。

## 5. `confirmation_items`（4 个子键）

`safety`(str/null)、`ethics`(str/null)、`equipment_availability`(str/null)、`expert_confirmation`(str/null)。未经确认的建议不写成可执行事实，进 `pending_human`。

## 6. 校验规则（`validate` 逐条执行）

1. result.json 可被 `json.load` 解析，顶层为 object。
2. 16 个顶层键全部存在。
3. `status` 合法、`case_id` 非空。
4. `interpretation` 五键存在且为数组；每个元素为 object；元素缺失子键 = error。
5. 每个元素的 `evidence` 为非空数组，每项 `locator`/`quote` 非空 = error；列表本身为空 = info（如实无此类结论需说明原因）。
6. `experiment_plan`/`evidence_and_risk`/`confirmation_items` 子键齐全。
7. `change_log` 非空数组，每项含 `ts/phase/action/summary`。
8. `runtime` 含 5 键。
9. 若 `--case` 传入 case.json，其 `output_contract` 的键也必须出现在 result.json。

## 7. locator 格式

- 表格：`data/batch_12.csv#L34`（行号）、`data/iris.csv#L2-L31`（区间）、`data/iris.csv#col=accuracy`（列）。
- PDF：`materials/paper.pdf#p3`（页）；导出文本后可用 `materials/paper.txt#L12`。
- 工作簿导出：`sheets/sheet_03.csv#L18`（sheet 名在导出 CSV 文件名中保留）。
- 引用必须逐字（quote 与材料原文一致）；同一指标两来源冲突时两条都记录并在 note 标 `source_conflict`。
