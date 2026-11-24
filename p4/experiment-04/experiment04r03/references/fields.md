# 字段定义与 analysis.json 接口规范

本文件定义“七类字段”“五维评分”“成本/时间口径”与 Agent↔脚本接口，供复用与复核。字段名必须按此命名，脚本据此计算、淘汰与排名。

## 1. 七类字段（每个候选方案统一填写）

| 字段 | 含义 | 填写要求 |
|---|---|---|
| assumptions | 假设 | 列出方案成立的前提；无法验证的标 pending |
| variables | 变量 | 独立/依赖/控制变量，至少给出可检验变量名 |
| controls | 对照 | 阳性/阴性/空白对照或基线条件 |
| steps | 步骤 | 可执行步骤序列，含准备与返工节点 |
| parameters | 参数 | 参数窗口与来源（设备极限/文献区间/经验） |
| resources | 资源 | 设备、耗材、人力、机时、排期；可含 cost/time 子表 |
| interpretation_criteria | 判读标准 | 成功/失败/需重做的判据 |

## 2. 五维评分（0–5，5 最优）

| 维度 | 考察 |
|---|---|
| goal_fit | 方案与研究目标/硬约束的匹配度 |
| risk | 风险越低分越高（触发条件、后果量级、缓解、回退是否齐全） |
| cost | 总成本相对预算越宽裕分越高 |
| feasibility | 设备可得、货期、技能、时间窗是否“本实验室现在可做” |
| evidence | 参数与结论的证据强度（文献/手册/报价/日志） |

权重默认 `goal_fit .25 / feasibility .25 / evidence .20 / cost .15 / risk .15`，可在 analysis.json 覆盖；缺失维度脚本自动重新归一化。

## 3. 成本与时间口径

- 成本六项：`machine_time`（机时）、`consumables`（耗材）、`labor`（人力）、`sample_prep`（样品准备）、`scheduling`（排期）、`rework`（返工）。
- 时间五项：`preparation`（准备）、`execution`（执行）、`scheduling`（排期）、`rework`（返工），合计为总时长。
- 区分一次性投入（设备改装、培训、标样）与按次消耗（试剂、机时、工日）。
- 脚本默认对 `resources.cost` / `resources.time` 内数值叶子求和；与报价表不符时，Agent 用 `cost_override` / `time_override` 覆盖并写 `note` + `evidence`。

## 4. 证据溯源（locator + quote）

每条证据 `{claim, locator, quote, kind}`：`locator` 形如 `materials/paper.md#p3` 或 `case.json#constraints.budget`；`quote` 为材料**逐字引用**；`kind` ∈ `literature | manual | quote | pilot | log | contract`。材料没给的写 `pending_human`/`not_reported`，不虚构 DOI、数值、报价。

## 5. analysis.json 接口（Agent 写，脚本读）

```json
{
  "case_id": "…",
  "research_question": {"testable_question": "…",
    "variables": {"independent": [], "dependent": [], "controlled": []},
    "hypothesis": "…"},
  "comparison_frame": {"objectives": [], "hard_constraints": [],
    "criteria": ["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
              "cost": 0.15, "risk": 0.15},
  "candidates": [{
    "plan_id": "P1", "name": "…", "version": "v1",
    "seven_fields": {"assumptions": "…", "variables": "…", "controls": "…",
      "steps": "…", "parameters": "…", "resources": "…", "interpretation_criteria": "…"},
    "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3, "feasibility": 4, "evidence": 3},
    "feasibility": {"equipment_available": "yes|no|partial", "lead_time_ok": "…", "notes": "…"},
    "risks": [{"trigger": "…", "consequence": "…", "magnitude": "…",
               "mitigation": "…", "fallback": "…"}],
    "evidence": [{"claim": "…", "locator": "…", "quote": "…", "kind": "…"}],
    "infeasible_reasons": [],
    "cost_override": {"total": 0, "currency": "CNY", "lines": [], "note": "…", "evidence": "…"},
    "time_override": {"total": 0, "unit": "h", "parts": {}, "computed": true}
  }],
  "selection": {"chosen": "P1", "ranking": [], "rationale": "…",
    "rejected": [{"plan_id": "P1", "reason": "…"}], "sensitivity": {}},
  "confirmations": [{"item": "…", "type": "safety|ethics|equipment|expert",
    "status": "pending|confirmed", "note": "…"}],
  "uncertainty": [], "sources": [], "contract_fills": {},
  "change_log": [{"seq": 1, "step": "…", "run": "…", "note": "…"}],
  "errors": [], "degradations": [], "pending_human": [], "model_requests_used": null
}
```

约定：

- `dimension_ratings` 五个键缺一不可；缺失时脚本按 0 计并重新归一化权重，不崩。
- `cost_override`/`time_override` 的 `total` 为 null 时脚本回落 `resources` 数值求和，再不可算则 `not_computable`。
- `change_log` 用 `run_id`（输入内容哈希）而非墙钟时间，保证字节级复现。
- `contract_fills`：若 case.json 有 `output_contract`，其键在此映射到 computed 值；未映射的键脚本填 null。
