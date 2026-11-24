# 输出契约精确定义

`result.json` 顶层字段与每个结论对象的字段语义。运行时以本文件为唯一字段词典，未知值写 `null` 或 `[]`，不改名、不删键；case.json 若含 `output_contract`，其全部字段必须原样回填（缺值 `null`/`[]`）。

## 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 必填，来自 case.json 的 `case_id`/`id`/`challenge_id`/`armiId` 或 task.md 的 `challenge_id`；无法解析时用输入目录名 |
| `status` | string | `ok`（结论可复核且无硬缺口）/ `partial`（有缺口但已识别）/ `insufficient_evidence`（无材料或材料不足）/ `input_error`（输入无法解析） |
| `task_type` | string | 固定 `experiment_result_interpretation` |
| `summary` | string | 1–3 句平实结论，不含未证实表述 |
| `interpretation` | object | 五段结论，见下 |
| `experiment_plan` | object | 实验方案，见下 |
| `evidence_and_risk` | object | 依据与风险，见下 |
| `confirmation_items` | object | 确认项，见下 |
| `sources` | array | 实际读取的材料文件清单（path/rel/size_bytes/ext） |
| `uncertainty` | array | 不确定性条目，见 `references/calibration.md` |
| `change_log` | array | 骨架→画像→结论→定稿各阶段的 `{ts, phase, action, detail}` |
| `errors` | array | 坏 JSON、解析失败等错误，不阻断其余材料 |
| `degradations` | array | 坏编码降级、二进制待导出、超限跳过等降级记录 |
| `limitations` | array | 材料缺失、对照缺失、重复数不足、超量程等限制 |
| `pending_human` | array | 需人工/专家确认的事项，不写成事实 |
| `runtime` | object | `started_at` / `finished_at` / `elapsed_seconds` |
| `fabrication_gate` | object | 禁编造门控：`must_not_invent` 数组 + `non_fabrication_checks` 数组 |

## interpretation（五段结论）

- `trends`：趋势条目数组
- `anomalies`：异常点条目数组
- `possible_causes`：可能原因条目数组
- `verification_steps`：复核步骤条目数组
- `followup_experiments`：后续实验建议条目数组

### 结论对象公共字段

| 字段 | 说明 |
|---|---|
| `evidence_grade` | `G0` 不可知（写 not_reported/pending_human）；`G1` 直接观测（可从原始文件逐字/逐行复算）；`G2` 可推导（由 G1 经明确统计方法推出）；`G3` 假设推断（需专家确认或补充实验） |
| `confidence` | `高` / `中` / `低`（缺对照/重复不足只能低） |
| `confidence_basis` | 置信由什么支撑：样本量、重复数、对照是否齐全、阈值是否先验 |
| `evidence` | 数组，每项 `{locator, quote}`，`quote` 为材料逐字摘录 |

### 分节补充字段

- `trends[*]`：`text`（方向+区间+对照+拟合方式+幅度+单位）、`direction`、`interval`、`fit_method`、`magnitude`、`unit`
- `anomalies[*]`：`text`、`object_locator`（样本/批次/通道/孔位/时间点定位）、`observed`、`expected`、`deviation`、`threshold`（方法+数值）、`exceeds_threshold`（bool）、`relative_to_control`
- `possible_causes[*]`：`text`、`supporting_evidence`、`excluding_evidence`、`confound_risk`、`testability`、`rank`
- `verification_steps[*]`：`target`（回查文件/字段/行或 sheet）、`expected_if_supported`、`decision_rule`
- `followup_experiments[*]`：`text`、`reason`、`expected_outcome`

## experiment_plan

`hypothesis`、`testable_question`、`variables`、`controls`、`steps`、`parameters`、`resources`、`interpretation_criteria`。完成标准：实验人员据此可执行或明确知道缺什么。

## evidence_and_risk

`evidence_basis`（文献/设备/历史记录依据）、`risk_and_anomaly_handling`（风险与异常处置）、`assumptions`（假设与边界）。完成标准：假设和边界可复核。

## confirmation_items

`safety`（安全）、`ethics`（伦理）、`equipment_availability`（设备可用性）、`expert_confirmation`（专家确认项）。完成标准：不把未经确认的建议写成可执行事实。

## 证据溯源硬要求

1. 每条 G1/G2/G3 结论必须有 `evidence`（locator + quote 逐字摘录）。
2. 同一指标两来源冲突时，两条都记录并标 `source_conflict`。
3. 不虚构引用、DOI、数值；缺失写 `pending_human` 或 `not_reported`。
4. 单位、方法、阈值有歧义时保留歧义写入 `uncertainty`，不擅自统一。
