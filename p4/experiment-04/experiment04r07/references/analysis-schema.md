# analysis.json 填写规范（Agent 与脚本的接口）

`assemble` 读取 `/app/work/analysis.json`，把它与 `case_pack.json` 合并成最终
`result.json` 与矩阵工件。脚本只做确定性算术与阈值淘汰；七类字段、五维评分与
证据由 Agent 读真实材料后填写。所有数值必须能指回材料的 locator + 逐字 quote，
材料没给就写 `pending_human` 或 `not_reported`，禁止编造。

## 顶层结构

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…",
    "variables":{"independent":[],"dependent":[],"controlled":[]},
    "hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[],
    "criteria":["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit":0.25,"feasibility":0.25,"evidence":0.20,
              "cost":0.15,"risk":0.15},
  "candidates": [ … ],
  "selection": {"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…",
    "rejected":[{"plan_id":"P1","reason":"…"}],"sensitivity":{}},
  "confirmations": [{"item":"…","type":"safety|ethics|equipment|expert",
    "status":"pending|confirmed","note":"…"}],
  "contract_fills": {"experimental_plan":{…},"evidence_and_risks":{…},
    "confirmation_items":[…]},
  "uncertainty": [], "sources": [], "change_log": [{"time":"…","step":"…","note":"…"}],
  "errors": [], "degradations": [], "pending_human": [], "limitations": []
}
```

## candidates[] 单个方案

```json
{
  "plan_id":"P1","name":"…","version":"v1",
  "seven_fields": {"assumptions":"…","variables":"…","controls":"…",
    "steps":"…","parameters":"…","resources":"…",
    "interpretation_criteria":"…"},
  "dimension_ratings": {"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
  "feasibility": {"equipment_available":"yes|no|partial","lead_time_ok":"…","notes":"…"},
  "risks": [{"trigger":"…","consequence":"…","magnitude":"…",
             "mitigation":"…","fallback":"…"}],
  "evidence": [{"claim":"…","locator":"materials/xxx.md#p3","quote":"逐字引用",
                "kind":"literature|manual|quote|pilot|log"}],
  "infeasible_reasons": [],
  "cost_override": {"total":0,"currency":"CNY","lines":[{"item":"…","amount":0,
    "basis":"once|per_run"}],"note":"…","evidence":"…"},
  "time_override": {"total":0,"unit":"h","parts":{"preparation":0,"execution":0,
    "scheduling":0,"rework":0},"computed":true}
}
```

## 约定与默认值

- 五维评分 0–5（5 最优）；缺维度时脚本自动把该维度权重重归一化，分数不虚高。
- `cost_override` / `time_override` 用于纠正脚本的通用算术：Agent 读真实报价/排期
  后以手工核对数字覆盖，并写 note + evidence。**不编造数字**。
- `cost_input` / `time_input`（可选）按六项成本、四项时间给原始分量，脚本求和；
  与 override 同时存在时 override 优先。
- 硬阈值来自 `comparison_frame.hard_constraints`，形如
  `{"dimension":"cost|time","op":"le|lt|ge|gt|eq|ne","value":数字}`；脚本据此淘汰。
- 每个关键结论/数值/参数范围必须挂 `evidence`（locator + 逐字 quote）；冲突来源
  两条都记录并标 `source_conflict`。
- `contract_fills` 若缺某键，脚本按选中的方案自动回填
  `experimental_plan` / `evidence_and_risks` / `confirmation_items` 三组默认形态。
