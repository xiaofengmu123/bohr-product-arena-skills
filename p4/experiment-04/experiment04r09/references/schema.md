# 字段定义与 analysis.json 契约（schema）

本文件是 Agent 与 `scripts/main.py` 之间的数据接口，也是「统一口径」的单一事实来源。脚本只做算术/淘汰/评分/渲染，不代写方案内容。

## 1. 七类字段（每个候选方案必填）

| 键 | 含义 | 填写要求 |
|---|---|---|
| `assumptions` | 假设 | 明确列出方案成立的前提（如设备可用、样品可得、文献区间外推） |
| `variables` | 变量 | 自变量 / 因变量 / 控制变量，写清单位与取值 |
| `controls` | 对照 | 阳性/阴性/空白/批次对照，写清与实验组的差异 |
| `steps` | 步骤 | 可执行流程，含关键操作与顺序 |
| `parameters` | 参数 | 数值区间并标注来源（设备极限/文献/经验） |
| `resources` | 资源 | 设备、耗材、人力、场地、审批，写清是否具备 |
| `interpretation_criteria` | 判读标准 | 成功/失败/中止的判据与阈值 |

## 2. 五维评分（0–5，5 最优）

| 键 | 含义 | 0–5 锚点 |
|---|---|---|
| `goal_fit` | 目标契合 | 是否直接回答研究问题、变量/对照是否对齐 |
| `risk` | 风险 | 高分=风险低且可控、有回退路径 |
| `cost` | 成本 | 高分=成本低且可核算 |
| `feasibility` | 可行性 | 高分=设备/耗材/档期/人员当下可做 |
| `evidence` | 证据 | 高分=关键参数与结论有文献/手册/报价/记录支撑 |

默认权重 `goal_fit 0.25, feasibility 0.25, evidence 0.20, cost 0.15, risk 0.15`；可在 analysis.json 覆盖。维度缺失时脚本对剩余维度重新归一化。

## 3. 成本与时间口径

成本 `cost.lines[]`，每条 `{item, amount, category, note?, evidence?}`；`category` 取 `one_time`（一次性）或 `per_run`（按次）。六项 item：`machine_time, consumables, labor, sample_prep, scheduling, rework`。脚本按 `总成本 = Σ一次性 + Σ按次 × runs` 计算。

时间 `time.parts`，五项：`preparation, scheduling, execution, rework, other`（单位 h）。脚本求和。

无法核算时：`cost.method = "not_computable"` 且 `total = null`；或提供 `total` + `method = "manual_override"` + `note/evidence`。脚本优先按 lines 计算，无 lines 才信 `total`。

## 4. 证据 / 风险 / 确认项格式

```json
{"claim":"…","locator":"materials/xxx.md#p3","quote":"逐字引用","kind":"literature|manual|quote|pilot|log"}
{"trigger":"…","consequence":"…","magnitude":"低|中|高","mitigation":"…","fallback":"…"}
{"item":"…","type":"safety|ethics|equipment|expert","status":"pending|confirmed","note":"…"}
```

每个关键结论/数值/参数范围必须挂 evidence（locator + 逐字 quote）；材料没给就写 pending_human / not_reported，不编造。

## 5. analysis.json 顶层结构

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…","variables":{"independent":[],"dependent":[],"controlled":[]},"hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[],"criteria":["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit":0.25,"risk":0.15,"cost":0.15,"feasibility":0.25,"evidence":0.20},
  "candidates": [{
    "plan_id":"P1","name":"…","version":"v1",
    "seven_fields": {"assumptions":"…","variables":"…","controls":"…","steps":"…","parameters":"…","resources":"…","interpretation_criteria":"…"},
    "dimension_ratings": {"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
    "feasibility": {"equipment_available":"yes|no|partial","lead_time_ok":"…","notes":"…"},
    "risks": [], "evidence": [], "infeasible_reasons": [],
    "cost": {"currency":"CNY","runs":1,"lines":[],"total":null,"method":"sum","note":"","evidence":""},
    "time": {"unit":"h","parts":{},"total":null,"method":"sum"}
  }],
  "selection": {"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}]},
  "confirmations": [], "sources": [], "uncertainty": [], "limitations": [],
  "contract_fills": {}, "change_log": [{"time":"…","step":"…","note":"…"}],
  "errors": [], "degradations": [], "pending_human": []
}
```

## 6. 硬约束与 status 语义

`comparison_frame.hard_constraints[]` 每条 `{id, field, op, value, unit?, source?}`；`field` 支持 `cost.total / time.total / cost.one_time / cost.per_run / time.execution`，`op` 支持 `<=, >=, <, >, ==`。脚本据此淘汰超预算/超工期方案，并把违规写入 `constraint_violations`。

`status`：`ok`（选择存在、证据充分、无待确认项）；`partial`（有选择但存在 pending 确认项/证据缺失/部分不可行）；`insufficient_evidence`（无候选或材料不足）；`input_error`（缺 case.json、坏 JSON、坏编码）。诚实优先，宁可 partial 不虚标 ok。
