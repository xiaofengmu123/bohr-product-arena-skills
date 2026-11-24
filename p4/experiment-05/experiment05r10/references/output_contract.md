# output_contract.md — result.json 字段定义

本文定义 `/app/output/result.json` 的字段语义、类型与完成标准。`case.json` 的 `output_contract` 若额外声明字段，必须原样回填（缺值给 `null`/`[]`），不改名、不删未知键。

## 顶层字段

| 字段 | 类型 | 含义与完成标准 |
|-|-|-|
| `case_id` | string | 来自 case.json 的 `case_id`/`id`/`challenge_id`，或 task.md 的 challenge_id；非空 |
| `status` | string | `ok`（结论完整且有据）\| `partial`（部分完成，说明缺口）\| `insufficient_evidence`（材料不足）\| `input_error`（输入不可解析） |
| `task_type` | string | 固定 `experiment-result-interpretation` |
| `summary` | string | 1–3 句平实结论，不含无证据断言 |
| `interpretation` | object | 五段结论，见下 |
| `experiment_plan` | object | 方案要素：假设、变量、对照、步骤、参数、资源、判读标准 |
| `evidence_and_risk` | object | 依据、风险与异常处置、假设 |
| `confirmation_items` | object | 安全/伦理/设备可用性/专家确认（未确认项不写成事实） |
| `sources` | array | 材料文件清单与来源定位 |
| `uncertainty` | array | 单位/方法/阈值歧义，保留原样不擅自统一 |
| `change_log` | array | 骨架→画像→结论→定稿各阶段记录 |
| `errors` | array | 坏 JSON、不可读文件等错误 |
| `degradations` | array | 坏编码、超量程、超限跳过等降级记录 |
| `limitations` | array | 对照缺失、重复数不足等限制 |
| `pending_human` | array | 需专家确认的事项 |
| `runtime` | object | `started_at`/`finished_at`/`elapsed_seconds` |

## interpretation 五段

| 键 | 条目必含字段 |
|-|-|
| `trends` | `claim`、`direction`、`evidence[]`；建议 `interval`、`control`、`fit`、`magnitude`、`unit` |
| `anomalies` | `claim`、`subject`、`value`、`threshold`(方法+数值)、`evidence[]` |
| `possible_causes` | `claim`、`supporting_evidence`、`excluding_evidence`、`confound_risk`、`testability`、`evidence[]` |
| `verification_steps` | `claim`、`target`、`expected_if_supported`、`decision_rule`、`evidence[]` |
| `followup_experiments` | `claim`、`reason`、`expected_outcome`、`evidence[]` |

`evidence[]` 每项：`locator`（如 `data/batch_12.csv#R34.C3`）+ `quote`（逐字摘录，非空）。同一指标两来源冲突时两条都记录并加 `source_conflict: true`。

## experiment_plan 八要素

`hypothesis`（假设）、`testable_question`（可检验问题）、`variables`（变量）、`controls`（对照）、`steps`（步骤）、`parameters`（参数范围）、`resources`（设备/材料/时间成本）、`interpretation_criteria`（判读标准）。完成标准：实验人员可据此执行或明确知道缺什么。

## evidence_and_risk

`evidence_basis`（文献/设备/历史记录依据，带 locator+quote）、`risk_and_anomaly_handling`（风险与异常处置）、`assumptions`（假设及其边界）。

## confirmation_items

`safety`、`ethics`、`equipment_availability`、`expert_confirmation` 四项。未经确认的建议写入 `expert_confirmation` 与 `pending_human`，不写成可执行事实。

## 评分维度映射（供自检对照，非评分承诺）

- 实际效果（任务完成度/正确性/完整性/可核验性）→ 第 5 节五段规范 + 第 6 节证据溯源。
- 可靠体验（异常处理）→ 第 9 节降级策略：数据缺失、单位不一致、重复数不足、对照缺失、超量程/饱和、结论冲突均需识别并记录。
- 生态贡献（文档完整性/资源可复用）→ 本文件字段定义 + 第 8 节自检清单 + 脚本参数说明。
