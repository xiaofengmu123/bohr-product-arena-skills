# 证据溯源规则与检查清单（evidence provenance）

本 Skill 的核心差异化：**每条结论都带 locator + 逐字 quote**，而不是只在文末列
参考文献。目标是把“候选参数怎么产生、每条约束从哪来、每条响应口径怎么定”都做成
可独立复核的证据链。

## 1. 溯源四要素

每条结论必须同时具备：

1. `claim_id`：稳定编号（如 `P1/C1/R1`），同一方案内不重复。
2. `statement`：一句可判定真假的陈述（不写泛泛经验，写“温度建议 80 °C”）。
3. `provenance[]`：至少一条证据，每条含 `locator`（材料内定位）+ `quote`
   （材料中的逐字原文）。
4. `derivation`：从 quote 到 statement 的推导/取舍一句话。

## 2. 定位（locator）与逐字（quote）规范

- locator 采用 `相对路径#定位`，例如 `materials/manual.md#p3`、
  `materials/paper.pdf#p7`、`notes/history.csv#row5`。PDF 用 `pdf-text-tool`
  实际读取后按页/段落定位；不得假设存在预提取答案层。
- quote 必须是材料中的逐字片段（`quote_type=verbatim`），禁止改写、转述或
  “大意如此”；材料确实没有时写 `not_reported` 并降级，不补造。
- 同一数值在多处出现且不一致：全部记录，`role=conflict`，`status=conflicting`。

## 3. claim_type 与 role 枚举

claim_type：`parameter_value`（建议值）、`parameter_domain`（可行域/上下限）、
`constraint`（设备/材料/安全约束）、`response_metric`（响应口径）、
`predicted_response`（预测值）、`cost_basis`（成本依据）、`risk`、`assumption`、
`control`、`resource`、`procedure`、`interpretation`。

provenance.role：`domain_bound`（界定上下限）、`value_support`（支撑建议值）、
`method`（测量/方法依据）、`conflict`（相互冲突）。

## 4. status 与 confidence 判定

| status | 何时用 |
|-|-|
| `supported` | quote 直接支撑 statement，且无冲突 |
| `inferred` | 由一条或多条 quote 推导得出 |
| `unverified` | 无直接材料支撑，仅经验/假设 |
| `conflicting` | 多来源不一致 |
| `pending_human` | 需专家/设备确认 |

confidence：`high`（原文直接且无歧义）/`medium`（推导/单一来源）/
`low`（仅经验规则或单一间接来源）。

## 5. 冲突与未核实处理（禁止编造）

- 冲突：两方都记，`conflict_count` 自增，方案 `risks`/`exception_handling`
  写清“按哪一方取值、为什么”，不得悄悄选有利的一边。
- 未核实：`verified=false`，登记到 `pending_human`，`uncertainty.unverified_values`
  记数值；方案正文把该结论标为“待确认”，不写成可执行事实。
- 材料缺失：相关字段留 `[]`/`null`，`status=insufficient_evidence`，
  写入 `limitations`，不编造数值或来源。

## 6. 提交前逐条勾选

- [ ] 每条关键参数有 `locator + quote` 且建议值落在可行域与设备/材料/安全边界内
- [ ] 每条响应指标有测量方法、方向、目标、可接受下限与预测方式披露
- [ ] 每条 `basis` 有 `source_id/locator/quote`
- [ ] 每条 claim 有 `provenance`（含逐字 quote），冲突显式标注
- [ ] `scripts/main.py evidence` 退出码 0（无溯源缺口）
- [ ] `scripts/validate_plan.py` 退出码 0 或仅警告，阻断错误已清零
- [ ] `scripts/main.py check` 退出码 0（result.json 契约与产物通过）
- [ ] `python3 -B scripts/selftest.py` 退出码 0（全链路自检通过）
