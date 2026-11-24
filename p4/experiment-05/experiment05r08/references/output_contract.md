# result.json 输出契约与字段定义

运行时产物为 `/app/output/result.json`（必填）、`/app/output/report.md`（可编辑报告，必填）、
`/app/output/evidence_map.json` 与 `/app/output/data_profile.json`（可选但建议保留）。

## 1. 顶层字段

| 字段 | 类型 | 含义 | 必填 |
|-|-|-|-|
| `case_id` | string | 来自 case.json 的 `case_id`/`id`/`challenge_id`/`armiId`，或 task.md 的 `challenge_id`；缺省回退输入目录名 | 是 |
| `status` | enum | `ok` / `partial` / `insufficient_evidence` / `input_error` | 是 |
| `task_type` | string | 固定 `experiment_result_interpretation` | 是 |
| `summary` | string | 1–3 句平实结论 | 是 |
| `data_form` | enum | 检测到的数据形态：`trace` / `tabular` / `workbook_paper` / `generic` | 是 |
| `interpretation` | object | 五段结论（见下） | 是 |
| `experiment_plan` | object | 实验方案（假设/变量/对照/步骤/参数/资源/判读标准） | 是 |
| `evidence_and_risk` | object | 依据与风险（依据、风险与异常处置、假设） | 是 |
| `confirmation_items` | object | 确认项（安全/伦理/设备可用性/专家确认） | 是 |
| `sources` | array | 所用材料清单（rel、size_bytes、sha256_head、role） | 是 |
| `uncertainty` | array | 单位/方法/阈值歧义与未定之处 | 是 |
| `change_log` | array | 骨架→画像→结论→定稿各阶段记录 | 是 |
| `errors` | array | 坏 JSON、坏编码、失败步骤 | 是 |
| `degradations` | array | 超限、缺对照、重复数不足等降级 | 是 |
| `limitations` | array | 结论局限与未解释部分 | 是 |
| `pending_human` | array | 需专家确认、不可自行判定事项 | 是 |
| `runtime` | object | `started`/`finished`/`elapsed_seconds` | 是 |

若 `/input/case.json` 含 `output_contract`，其全部字段**原样回填**（缺值写 `null` 或 `[]`），不改名、不删未知键。

## 2. interpretation（五段结论）

- `trends`：每项写 `direction`（增/减/稳定/非单调/有拐点）、`interval`（所依据区间）、
  `control`（对照）、`fit`（拟合方式）、`magnitude`（幅度与单位）。禁止只复述读数。
- `anomalies`：每项写 `object`（样本/批次/通道/孔位/时间点定位）、`observed`、`expected`、
  `deviation`、`threshold`（含方法名与数值，如 `z>3.0`、`IQR 1.5×`、`MAD mod-z>3.5`、Grubbs）。
- `possible_causes`：按证据强度/可检验性排序；每项含 `supporting_evidence`、`excluding_evidence`、
  `confound_risk`、`testability`。无排除过程的机制猜测必须标低置信并写明缺哪步验证。
- `verification_steps`：可执行动作，每项含 `target`（回查文件/字段/行或 sheet）、
  `expected_if_supported`、`decision_rule`。禁止“检查数据质量”式空话。
- `followup_experiments`：每项含 `reason`、`expected_outcome` 与具体改动（补对照/加密采样/调参数/扩重复）。

## 3. 证据溯源（硬要求）

每条关键结论/数值/图元绑定 `evidence` 数组，每项含：
- `locator`：如 `materials/bend_batch_03.csv#L34`、`materials/paper.md#p3`；
- `quote`：材料中的**逐字摘录**，不改写、不翻译。

同一指标两来源不一致时两条都记录并标 `source_conflict`；缺失写 `not_reported` 或进 `pending_human`。

## 4. 状态判定

- `ok`：材料足以支撑五段结论，且证据齐全、可复算。
- `partial`：已产出部分可复核结论，但存在未解释部分或证据缺口。
- `insufficient_evidence`：无材料或材料不足以做出任何结论；写合法骨架并列出缺口。
- `input_error`：输入不可解析（case.json 损坏且 task.md 缺失等），记录到 `errors`。

状态必须诚实：绝不编造数据、机理、重复数、软件状态或评测分数。
