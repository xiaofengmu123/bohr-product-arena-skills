# 方案字段语义、证据规范与回退结构

本文件定义 `result.json` 的字段语义、证据溯源规则、以及无 `output_contract` 时的回退结构。运行时若 `case.json` 携带 `output_contract`，则以契约键为准，逐键原样回填（缺值 `null`/`[]`，不改名、不删未知键）。

## 1. 顶层信封（每个 result.json 都必须有）

| 键 | 类型 | 语义 |
|-|-|-|
| `case_id` | string | 输入标识：case.json 的 `case_id`/`challenge_id` → task.md 的 `challenge_id` → `"unknown"`（并列 `pending_human`） |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error`，必须诚实 |
| `sources` | list | 用到的材料清单（相对 /input 路径 + 页/段定位） |
| `uncertainty` | list | 冲突、缺口、约束冲突的双方与影响 |
| `pending_human` | list | 需专家/人工确认的事项，未确认不得写成事实 |
| `change_log` | list | `{ts, event, detail}`；`ts` 必须为逻辑步号 `L0001`、`L0002`…（**禁用墙钟**，保证确定性） |
| `errors` | list | 解析/写盘等运行错误 |
| `degradations` | list | 降级记录（缺软件、超大文件跳过等） |
| `determinism` | object | `{policy, input_fingerprint, contract_source}`；证明同输入可复现 |
| `skill` | object | `{name, version}`，用于版本核验 |

## 2. 主结构（回退契约 `FALLBACK_CONTRACT`）

题目要求方案覆盖六类要素，回退结构把六要素放进 `plan`，并单列依据与风险、确认项：

```text
plan
├── hypothesis_and_variables   假设与变量（可检验假设；自/因/混杂变量的操作定义、单位、量程、测量方式）
├── controls                    对照组（空白/阳性/阴性/剂量梯度；每个对照排除哪类混杂）
├── equipment_and_materials     设备材料（型号、量程、耗材规格、试剂；可替代/不可替代）
├── time_and_cost               时间成本（步骤时长、并行、样本量、预算、排期算术）
├── parameter_ranges            参数范围（关键参数、单位、允许区间、越界处置、依据来源）
├── risks_and_anomalies         风险与异常（失败模式、判据、止损点、替代路径）
├── steps                       [步骤列表，绑定设备/试剂/时长/判读]
└── readout_criteria            判读标准（终点指标、统计口径、达标阈值）
evidence_and_risks
├── evidence_map                [{locator, quote, claim, source_conflict?}]
├── risks                       [{failure_mode, criteria, stop_loss, alternative}]
└── assumptions                 [显式假设]
confirmations
├── safety / ethics / equipment_availability / expert_review   [确认项列表]
```

若题目契约指定了其他主结构（如把六要素拆成不同键名），按契约键回填，并把六要素的覆盖关系写进 `uncertainty` 或 `change_log` 说明，便于复核。

## 3. 证据溯源（硬要求）

- 每个关键结论/数值/型号/价格/排期，都带 `locator`（如 `materials/paper.md#p3`、PDF 页码、合同文件段）+ 逐字 `quote`（材料原句）。
- 同一指标两个来源不一致时，两个都记并标 `source_conflict: true`，说明冲突双方与影响。
- 材料没有的数值、型号、价格、批准一律写 `not_reported` 或列入 `pending_human`；**不虚构引用、DOI、数值、价格、排期**。
- 预算/排期/参数若来自案例内置“前瞻性合同”，只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。

## 4. 数值与算术规范

- 参数必须带单位与量程（允许区间），越界处置写明；`min`/`max` 不得颠倒（`scripts/main.py` 会自动检查）。
- 时间/预算需给出步骤时长、并行关系、样本量与总预算，排期算术要能被复算；候选方案做参数/量程/预算/排期算术校验后再择优。
- 测量值与估算值分开标注：实测标 `measured`、文献标 `literature`、预实验标 `pilot`、估算标 `estimate`、案例给定约束标 `case_given`。
