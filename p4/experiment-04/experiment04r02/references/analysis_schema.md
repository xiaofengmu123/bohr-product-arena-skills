# analysis.json 填写规范（Agent 与脚本的接口）

`assemble` 只做确定性的算术、淘汰、评分、敏感性分析与产物渲染；所有科学判断由 Agent 读材料后写入本文件。文件写入 `/app/work/analysis.json`，UTF-8。

## 顶层结构

```json
{
  "case_id": "…",
  "research_question": {"testable_question": "…", "variables": {"independent": [], "dependent": [], "controlled": []}, "hypothesis": "…"},
  "comparison_frame": {"objectives": [], "hard_constraints": [], "criteria": ["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20, "cost": 0.15, "risk": 0.15},
  "candidates": [ … ],
  "selection": {"chosen": null, "ranking": [], "rationale": "…", "rejected": [], "sensitivity": {}},
  "confirmations": [{"item": "…", "type": "safety|ethics|equipment|expert", "status": "pending|confirmed", "note": "…"}],
  "uncertainty": [], "sources": [], "contract_fills": {}, "change_log": [],
  "errors": [], "degradations": [], "pending_human": [], "limitations": []
}
```

- `selection` 内的 chosen/ranking 由脚本重算，Agent 只需写 `rationale`（可辩护的舍弃理由）。
- `contract_fills`：若 case.json 含 `output_contract`，其每个键可在此显式给值；不写则由脚本从选定方案合成。
- `sources`：全局证据 `{locator, quote, kind}`；候选级证据写在 candidates[].evidence。

## 七类字段（每个候选方案必须填同一组键）

`seven_fields`：`assumptions`（假设）、`variables`（变量）、`controls`（对照）、`steps`（步骤）、`parameters`（参数）、`resources`（资源）、`interpretation_criteria`（判读标准）。

## 五维评分（0–5，5 最优，方向统一）

`dimension_ratings`：`goal_fit`、`risk`（5=风险最低）、`cost`（5=成本最低）、`feasibility`、`evidence`。缺省维度由脚本自动从权重中剔除并重新归一化。

## 成本六项口径

`cost` 或 `cost_override` 必须覆盖：机时 `machine_time`、耗材 `consumables`、人力 `labor`、样品准备 `sample_prep`、排期 `scheduling`、返工 `rework`；每项区分一次性投入 `one_time` 与按次消耗 `per_run`。

```json
"cost_override": {
  "total": 41200, "currency": "CNY",
  "lines": [
    {"item": "machine_time", "amount": 12000, "kind": "per_run", "quantity": 12, "unit_cost": 1000},
    {"item": "consumables", "amount": 9000, "kind": "per_run"},
    {"item": "labor", "amount": 16000, "kind": "one_time"}
  ],
  "note": "手工核对报价后的覆盖值", "evidence": "materials/quote.pdf#p2"
}
```

读不到真实数字时写 `cost_override.total=null` 或省略 `cost_override`；脚本回退 `method=not_computable, total=null` 并记 `pending_human`。**禁止编造数字。**

## 时间口径

`time_override`：`total`（h）、`unit`、`parts`；覆盖准备 `preparation`、排期 `scheduling`、执行 `execution`、表征排队 `queueing`、返工 `rework` 五项。

## 硬阈值

`comparison_frame.hard_constraints` 每项 `{id, metric, op, value, unit}`。脚本支持的 `metric`：`total_cost`、`total_time`、`equipment_available`、`safety_approved`、`ethics_approved`；`op`：`<= < >= > == !=`。超预算、超时间窗、必需设备不可得、安全/伦理未获批均触发淘汰。

## 证据溯源

每个关键数值/参数范围/结论都必须挂 `{claim, locator, quote, kind}`；`quote` 为材料逐字原文，`locator` 形如 `materials/paper.md#p3` 或 `materials/quote.pdf#p2`。同一指标多来源冲突时两条都记录并标 `source_conflict`。
