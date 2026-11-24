# evidence_map 证据溯源规范（本变体核心：每条结论都挂 locator + 逐字 quote）

本 Skill 的差异化设计是把“证据”从散落的段落提升为**可查询、可复核的台账（ledger）**。Agent 在 analysis.json 中写入的证据条目，脚本 assemble 会归一化成全局 `evidence_map`，并自动统计覆盖度与未报告缺口。

## 1. 归一化后的 claim 结构

```json
{
  "claim_id": "C7",
  "claim": "路线A机时费为 300 CNY/小时",
  "subject": "P1",
  "field": "cost",
  "assertion_type": "fact",
  "strength": "reported",
  "locator": "materials/quote_A.md#L12",
  "quote": "- 路线A机时费：300 CNY/小时。",
  "kind": "quote",
  "source_ref": "S1",
  "status": "evidenced",
  "conflict": null
}
```

字段含义：

| 字段 | 含义 | 取值/示例 |
|---|---|---|
| `claim_id` | 全局唯一，稳定排序后分配 | `C1`, `C2`, … |
| `claim` | 被断言的结论/数值本身 | 一句可判定真伪的话 |
| `subject` | 结论属于谁 | `frame`、`selection` 或候选 `P1/P2/P3` |
| `field` | 结论属于哪类事实 | `cost`、`time`、`parameters`、`risk`、`feasibility`、`goal_fit`… |
| `assertion_type` | 断言性质 | `fact`（观测/报价）、`inference`（推导）、`estimate`（估算）、`assumption`（假设） |
| `strength` | 证据强度 | `established`、`preliminary`、`prospect`、`reported`、`not_reported` |
| `locator` | 材料定位 | `materials/xxx.md#p3` 或 `materials/xxx.pdf#p5` |
| `quote` | 逐字引用（不得改写） | 直接复制材料原文片段 |
| `kind` | 来源类型 | `literature`、`manual`、`quote`、`pilot`、`log` |
| `source_ref` | 指向 `sources[]` 的 ref_id | `S1` |
| `status` | 脚本判定 | `evidenced`（quote 或 locator 非空）/ `not_reported` |
| `conflict` | 冲突记录 | `null` 或 `{"other_ref":"S2","resolution":"…"}` |

## 2. coverage 统计

脚本自动汇总：

```json
{
  "total_claims": 14,
  "evidenced": 12,
  "not_reported": 2,
  "conflicts": 1
}
```

- `not_reported > 0` 的 claim 会**自动进入 `pending_human`**，绝不静默跳过。
- `conflicts > 0` 时说明同一指标存在两个来源不一致，两个来源都保留并标 `source_conflict`，不隐藏。

## 3. conclusion_map：结论 → claim 的反向索引

```json
{
  "chosen": ["C9", "C10"],
  "rejected_P1": ["C3"],
  "hard_threshold_decisions": ["C1", "C2"]
}
```

- `chosen` 指向支撑“选中某方案”的 claim。
- `rejected_<plan_id>` 指向支撑“舍弃某方案”的 claim（超预算/超时间窗/设备不可得）。
- `hard_threshold_decisions` 指向支撑淘汰判定的成本/时间 claim。

## 4. 写入规则（Agent 必须遵守）

1. **每条关键结论、数值、参数范围都要写一条 evidence 条目**；只写结论不写 quote 会被脚本标 `not_reported`。
2. `quote` 必须逐字复制材料原文；材料是 PDF 时先提取文本再复制，不许凭记忆改写。
3. 同一指标两个来源不一致时，写两条 claim 并在其中一条标 `conflict`，或写一条带 `conflict` 字段的 claim。
4. 材料没给的数值：`locator`/`quote` 留空，并写进 `pending_human`，不编造。
5. `assertion_type` 与 `strength` 要诚实：报价单数字是 `fact`，外推的耗时是 `estimate`；只有原文明确支持才用 `established`。

## 5. 与“结论强度靠语气”的区别

普通写法：“方案A更可行”。本规范写法：

> `C7` [P1/cost] fact/`reported`：“路线A机时费为 300 CNY/小时” → `materials/quote_A.md#L12`，原文：“- 路线A机时费：300 CNY/小时。”

这样结论换一句话也能指回同一材料，读者能独立复核，方案一换也能说清为什么改选。
