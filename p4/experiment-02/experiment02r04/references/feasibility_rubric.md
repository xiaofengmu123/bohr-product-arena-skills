# 可行性审查口径（feasibility_rubric）

本文定义七类约束的判定口径、状态枚举、证据定位格式与算术规则。所有判断必须落到
方案原文的具体章节、参数与清单行；“方案没写”一律记为 `missing`，不得默认满足。

## 1. 七类约束与判定来源

| 维度 | 审查问题 | 判定依据来源 |
|---|---|---|
| `assumptions_variables` | 假设是否写明、变量是否可测量、混淆变量是否处理 | 方案原文、方法学章节 |
| `controls` | 对照组的类型、数量、随机化/盲法是否成立 | 方案原文、SOP |
| `equipment_materials` | 设备型号/精度/附件、耗材规格、货期与库存 | 设备清单、采购记录、预约合同 |
| `time_cost` | 每步设备占用、人工操作、等待周期、检测周转与总成本 | 步骤表、合同、历史排期 |
| `parameter_ranges` | 参数上下限、容差、单位、是否有依据 | 文献、设备手册、SOP |
| `risks_exceptions` | 设备故障、批次差异、样本不足、参数越界的降级路径 | 方案、SOP、历史记录 |
| `ethics_safety` | 动物/人体样本、危险试剂、个人数据的审批层级与时长 | 伦理规范、审批表单、安全规程 |

## 2. 审查项结构

```json
{
  "id": "eq-1",
  "dimension": "equipment_materials",
  "claim": "……",
  "verdict": "conditional",
  "resource_state": "需采购",
  "conditions": ["采购前需确认……"],
  "evidence": [{"locator": "case.json#plan.0.steps[2]", "quote": "逐字原文", "kind": "observed"}],
  "confidence": "medium",
  "needs_expert": false,
  "notes": []
}
```

- `verdict` 枚举：`ok | conditional | blocked | unconfirmed | missing | conflict`。
- `ok`：方案原文与依据能相互印证且无缺口。
- `conditional`：可行但必须满足 `conditions`（逐条可执行，写清“谁、确认什么、约多久”）。
- `blocked`：缺关键资源/审批/样本，写清缺什么。
- `unconfirmed`：证据不足或需专家确认，明确“需谁、确认什么”，不得写成已确认事实。
- `missing`：方案未写该信息。
- `conflict`：同一指标多个来源不一致，全部记录并标 `source_conflict`。
- `resource_state` 仅资源维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `ok/conditional/blocked/conflict` 必须带 `evidence`（含 `locator` + 逐字 `quote`）。

## 3. 证据定位格式（locator）

- `case.json#plan.<i>.steps[<j>]`
- `case.json#contracts.<key>`、`case.json#parameters.<key>`
- `materials/<name>.md#<标题或行>`
- `sources/<name>.pdf#p<页>`
- `task.md#<行>`

`evidence.quote` 必须是材料中的**逐字原文**；过长时截取决定性子句并给定位，
不转写、不补全。冲突信息不隐藏，两条都记录。

## 4. 算术规则（用 scripts/review.py，不做口头估计）

- 时长求和：`sum_time(items, key="hours")` 返回 `{total, stated, missing, detail}`；
  缺失步骤计入 `missing`，**不默认当 0**，需另写假设或转 `pending_human`。
- 参数范围：`check_range(value, lo, hi)` 返回 `in_range | out_of_range | missing`。
- 样本量：`check_sample_size(required, available, loss_rate, buffer)`，
  可用量 = `available × (1 − loss_rate)`，需求量加 `buffer` 后比较，返回缺口。
- 每个数值结论必须写明：来源数值（observed）、计算公式（derived）或显式假设
  （assumed）；三者皆无则属 `unreported`，进入 `unverifiable_claims`，不得补造。

## 5. 证据置信度

- `observed + locator + quote` → `high`
- `derived + formula`（有输入与公式）→ `medium`
- `observed` 仅 locator、或 `derived` 无公式、或 `assumed` → `low`
- `unreported` → `insufficient`

详见 `references/limitations_and_uncertainty.md`。
