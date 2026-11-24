# 三层中间表示（IR）Schema 说明

本 Skill 用三层结构化中间表示承载“事实 → 方案 → 比较”的推理，每层独立可复核。Agent 只改 IR，再跑 `assemble` 派生最终 `result.json`；**不直接改 result.json**。

## 1. case_pack.json（事实层）

由 `inspect` 生成，含：

```json
{
  "schema": "case_pack",
  "schema_version": "1.1",
  "case_id": "…",
  "status": "input_error|insufficient_evidence|partial",
  "input_hash": "…",
  "created_at": "…",
  "input_files": {"case": "case.json", "task": "task.md", "materials": ["…"]},
  "output_contract": {"<字段>": null},
  "candidates_raw": [{"plan_id": "P1", "…": "…"}],
  "candidate_count": 3,
  "materials_index": [{"path": "…", "kind": "pdf|text|csv|…", "size_bytes": 0,
                       "extracted": true, "note": "…"}],
  "errors": [],
  "degradations": []
}
```

`output_contract` 原样保留；无显式契约时脚本回填 `experimental_plan`、`evidence_and_risks`、`confirmation_items` 三组。`input_hash` 是对全部输入文件（相对路径 + 内容 SHA256）排序后的整体哈希，用于复跑比对。

## 2. plans_model.json（方案层）

Agent 逐方案填写；`init-models` 会先落一版骨架：

```json
{
  "schema": "plans_model",
  "schema_version": "1.1",
  "case_id": "…",
  "candidates": [{
    "plan_id": "P1",
    "name": "…",
    "version": "v1",
    "seven_fields": {
      "assumptions": "…", "variables": "…", "controls": "…", "steps": "…",
      "parameters": "…", "resources": "…", "interpretation_criteria": "…"
    },
    "dimension_ratings": {"goal_fit": 4, "risk": 3, "cost": 3,
                          "feasibility": 4, "evidence": 3},
    "feasibility": {"equipment_available": "yes|no|partial",
                    "lead_time_ok": "…", "notes": "…"},
    "cost": {"method": "computed|override|not_computable",
             "currency": "CNY", "total": null,
             "lines": [{"item": "…", "kind": "machine_time|consumable|labor|prep|booking|rework|other",
                        "unit_cost": 0, "quantity": 0, "amount": 0,
                        "once_or_per_run": "once|per_run", "evidence_ref": "E1"}]},
    "time": {"unit": "h", "override_total": null,
             "parts": {"prep": 0, "run": 0, "booking": 0, "rework": 0}},
    "risks": [{"trigger": "…", "consequence": "…", "magnitude": "…",
               "mitigation": "…", "fallback": "…", "evidence_ref": "E2"}],
    "evidence_refs": ["E1", "E2"],
    "infeasible_reasons": []
  }],
  "evidence_registry": [{
    "id": "E1", "claim": "…", "locator": "materials/xxx.md#p3",
    "quote": "逐字引用", "kind": "literature|manual|quote|pilot|log"
  }]
}
```

成本行：`amount` 优先；缺省时用 `unit_cost × quantity`。`kind` 必须覆盖统一口径六项（机时/耗材/人力/准备/排期/返工）。证据一律引用 `evidence_registry.id`。

## 3. comparison_model.json（比较层）

```json
{
  "schema": "comparison_model",
  "schema_version": "1.1",
  "case_id": "…",
  "comparison_frame": {"objectives": [], "hard_constraints": [], "criteria": ["goal_fit","risk","cost","feasibility","evidence"]},
  "weights": {"goal_fit": 0.25, "feasibility": 0.25, "evidence": 0.20,
              "cost": 0.15, "risk": 0.15},
  "thresholds": [{"path": "cost_total", "op": "gt|lt|gte|lte|eq|neq",
                  "value": 100000, "action": "eliminate|flag", "reason": "…"}],
  "selection": {"chosen": "P2", "ranking": ["P2","P1","P3"],
                "rationale": "…", "rejected": [{"plan_id": "P1", "reason": "…"}],
                "sensitivity": {}},
  "confirmations": [{"item": "…", "type": "safety|ethics|equipment|expert",
                     "status": "pending|confirmed", "note": "…"}],
  "uncertainty": [{"item": "…", "level": "low|medium|high", "note": "…"}],
  "change_log": [{"time": "…", "step": "…", "note": "…"}]
}
```

`thresholds.path` 支持点路径（如 `cost_total`、`time_hours`、`equipment_available`、`dimension_ratings.feasibility`）；`action=eliminate` 命中即硬淘汰，`action=flag` 仅记录。

## 4. 派生规则（assemble 自动执行）

- 成本总额：Σ 成本行（`amount` 或 `unit_cost×quantity`）；`method=override` 用声明 total；无行无声明 → `not_computable`，total=null。
- 时间总额：`parts` 四段求和；有 `override_total` 用覆盖值。
- 加权评分：先全局归一化权重；某方案缺某维度评分时按该方案可用维度重新归一化。
- 排名：可行方案按加权分降序，同分按 plan_id 升序（确定性）。
- 敏感性：对每个维度 ±0.05 扰动并重排，记录 ranking 是否变化与 chosen 是否稳定。
- 状态：`input_error`/`insufficient_evidence` 由 inspect 决定；否则存在 errors/degradations/pending_human/confirmations 或 chosen 为空 → `partial`，其余 → `ok`。
