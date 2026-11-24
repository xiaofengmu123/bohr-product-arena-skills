# result.json 输出契约（字段定义）

本文件是 `SKILL.md` 第 3 节字段的精确定义。运行时若 `case.json` 含 `output_contract`，其全部字段由 `scripts/main.py skeleton` 原样回填（缺值给 `null`/`[]`），不改名、不删未知键；本文件定义的字段在此基础上叠加。

## 顶层

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `case_id` | string | 是 | 案例标识，来自 case.json 的 `case_id/id/challenge_id/armiId` 或 task.md 的 challenge_id |
| `status` | enum | 是 | `ok`（结论完整可复核）/`partial`（部分完成）/`insufficient_evidence`（材料不足）/`input_error`（输入坏） |
| `task_type` | string | 是 | 固定为 `experiment_result_interpretation` |
| `analysis_mode` | enum | 是 | 数据形态：`curve_trace`/`tabular`/`ml_validation`/`workbook_paper`/`mixed`/`unknown`，由画像判定 |
| `summary` | string | 是 | 1–3 句平实结论，不带装饰语 |
| `sources` | array | 是 | 所用材料清单，每项 `{locator, kind, note}` |
| `uncertainty` | array | 是 | 单位/方法/阈值歧义，逐条说明 |
| `change_log` | array | 是 | 骨架→画像→结论→定稿各阶段，每项 `{ts, stage, detail}` |
| `errors` | array | 是 | 坏 JSON 等可继续处理的错误 |
| `degradations` | array | 是 | 坏编码、降级读取等 |
| `limitations` | array | 是 | 缺失材料、对照缺失、重复数不足等 |
| `pending_human` | array | 是 | 未经专家确认、不写成事实的事项 |
| `runtime` | object | 是 | `{started_at, finished_at, elapsed_seconds, model_requests_used, environment}` |
| `reproducibility` | object | 是 | `{input_fingerprint, profile_fingerprint, content_fingerprint, rerun_verified, sorting, random_seed}` |

## interpretation（五段结论）

每段是数组，元素为结论对象；对象可含额外字段（如 `metric`、`unit`、`batch`、`channel`）。

- `trends[]`：`{statement, interval, comparison, fit_method, magnitude, unit, direction, evidence[]}`
- `anomalies[]`：`{object_locator, observed, expected, deviation, threshold_method, threshold_value, exceeded, relative_to_control, evidence[]}`
- `possible_causes[]`：`{cause, rank, confidence, supporting_evidence[], excluding_evidence[], confound_risk, testability}`；按证据强度/可检验性升序排列（rank=1 最强）
- `verification_steps[]`：`{target, expected_if_supported, decision_rule}`；target 必须可执行（文件/字段/行/sheet/设备日志/平行重复）
- `followup_experiments[]`：`{change, reason, expected_outcome}`

`evidence[]` 每项：`{locator, quote}`。`locator` 形如 `materials/paper.md#p3`、`data/batch_12.csv#L34`；`quote` 为材料逐字摘录，不得改写。

## experiment_plan（实验方案）

- `hypothesis`：假设
- `testable_question`：可检验问题
- `variables[]`：变量（自变量/因变量/协变量）
- `controls[]`：对照设置
- `steps[]`：步骤
- `parameters[]`：参数与范围
- `resources[]`：设备、材料、时间、成本
- `interpretation_criteria[]`：判读标准（阈值与方法）

## evidence_and_risk（依据与风险）

- `evidence_basis[]`：文献/设备/历史记录依据，带 locator
- `risk_and_anomaly_handling[]`：风险与异常处置
- `assumptions[]`：假设与边界

## confirmation_items（确认项）

- `safety[]`、`ethics[]`、`equipment_availability[]`、`expert_confirmation[]`
- 完成标准：不把未经确认的建议写成可执行事实；需确认的写入 `pending_human`。
