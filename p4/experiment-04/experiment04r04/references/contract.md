# 字段、状态与不确定性契约（contract）

本文件是 SKILL.md 与脚本之间的字段定义与诚实降级口径，供 Agent、复核者与后续维护者复用。

## 1. 七类字段（每个候选方案必填，同一张矩阵）

| 字段 | 含义 | 常见来源 |
|---|---|---|
| assumptions | 假设（默认成立的边界条件） | 材料、文献、题面约束 |
| variables | 变量（自变量/因变量/控制变量） | 研究问题、材料 |
| controls | 对照（阳性/阴性/空白/历史对照） | 方案、文献 |
| steps | 步骤（可执行的顺序操作） | 方案、设备手册 |
| parameters | 参数（数值范围 + 来源） | 设备极限/文献区间/报价 |
| resources | 资源（设备/耗材/场地/人员） | 合同、报价、排期 |
| interpretation_criteria | 判读标准（成功/失败/停摆阈值） | 方案、文献 |

七类字段任一缺失 → `pending_human` + status 最多为 `partial`，不得标 `ok`。

## 2. 五维评分（0–5，5 最优）

goal_fit（目标契合）、risk（风险，越高越安全）、cost（成本）、feasibility（可行性）、evidence（证据强度）。权重可自定义；脚本按可用权重重新归一化。评分必须能指回 evidence，纯主观打分标 `not_assessed`。

## 3. 成本与时间口径

- 成本六项：equipment_time（机时）、consumables（耗材）、labor（人力）、sample_prep（样品准备）、scheduling（排期）、rework（返工）。
- 时间四项：prep、scheduling、execution、rework。
- 每项标 `mode`：`one_time`（一次性投入）或 `per_run`（按次消耗）；无来源数值标 `value_kind: not_reported`。
- 脚本方法：`line_sum`（明细求和）/`given_total`（材料给定）/`override`（Agent 手工核对覆盖）/`not_computable`（total=null，写 pending_human）。

## 4. 证据与 value_kind

每个关键结论/数值/参数必须挂 `evidence`：`{claim, locator, quote, kind, value_kind}`。脚本逐字核验 `quote` 是否出现在 `locator` 指向的文本文件（跳过空白差异）；未命中标 `quote_verified=false`。

- `value_kind`：`measured`（实测）、`contract`（合同/报价）、`estimated`（估算）、`derived`（推导）、`not_reported`（缺数据）。
- `kind`：`literature | manual | quote | pilot | log`。

## 5. 状态机（只降级，不升级）

```text
ok  ── 证据完整且逐字核验通过、七类字段/五维齐全、无硬阈值冲突
partial ── 有缺口但结论可用（缺证据、缺评分、设备未确认、全部不可行等）
insufficient_evidence ── 无候选方案/材料不足，无法比较
input_error ── 缺 case.json、坏 JSON、坏编码
```

判定顺序：无候选 → insufficient_evidence；全部不可行 → partial 且 chosen=null；有候选但存在未核验证据/缺字段/缺评分/缺 analysis.json → partial；否则 ok。

## 6. 不确定性、局限与未决项（本版重点）

- `uncertainty[]`：每项 `{plan_id?, item, basis, impact, confidence: high|medium|low}`；脚本自动追加未核验证据与缺评分项。
- `limitations[]`：每项 `{plan_id?, scope, cause, impact, mitigation}`；任何非 ok 状态都必须有一条解释结论强度。
- `pending_human[]`：需人工补齐的缺口（报价、设备档期、安全/伦理审批、专家确认），未确认不得写成可执行事实。
- `confirmations[]`：安全/伦理/设备/专家四项，status ∈ `pending|confirmed`。

## 7. 硬阈值与敏感性

- 硬阈值来自 `comparison_frame.hard_constraints`：`{field, op, value}`，支持 cost/time 的 `max/<=` 与 equipment 的 `required`。超阈值候选进 `infeasible_reasons`，不进入 chosen。
- 敏感性：每个维度权重 ±0.05 后重新归一化排名，记录 `chosen_unchanged`；全部扰动下选择不变 → `stable=true`。
- 全部候选不可行时不强行选择：chosen=null，rationale 说明，status=partial。
