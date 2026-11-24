# 中间表示与复核规范（plan-model / calc / finding / contract）

本文件定义 Skill 的结构化中间表示（`plan_model.json`）与派生记录，使审查"可复算、可复核"。目标是：任何人拿到 `plan_model.json` + `calc_trace.json` 即可重放全部数值，不必重读自然语言。

## 1. plan-model/1.0 顶层

```json
{
  "schema_version": "plan-model/1.0",
  "case_id": "…", "challenge_id": "…", "title": "…",
  "case_file": "case.json", "input_files": [{"path","bytes","ext"}],
  "plans": ["见 §2"], "registry": {"<norm_id>": "见 §3"},
  "domains": {"<参数名>": {"min","max","unit","allowed","locator","raw"}},
  "context": {"budget","planned_start","decision_date","window",
              "personnel","sample_inventory","ethics","hazard_declared"},
  "issues": [{"id","where","kind","detail"}], "notes": []
}
```

`registry` 键用规范化 id（小写、去非字母数字）。每个计划与资源的 `locator` 都指向 `case.json` 原文位置。

## 2. plan 记录

```json
{
  "plan_id", "label", "locator",
  "goal", "hypothesis", "assumptions": [{"text","locator"}],
  "variables": [{"name","role","unit","levels","locator"}],
  "controls": [{"text","locator"}], "criteria": [{"text","locator"}],
  "samples": [{"id","n","per_arm","unit","volume","locator"}],
  "parameters": [{"name","value":{"value","raw","dim","unit"},"locator"}],
  "steps": [{
     "step_id","index","name","locator",
     "duration","operator","wait": "同 quantity 结构",
     "equipment": [{"id","minutes"}], "materials": [{"id","qty"}],
     "params": ["同 parameters"], "samples": "…", "depends_on": ["…"]
  }],
  "risks": [{"text","mitigation","locator"}],
  "personnel": [{"role","n","hours_per_week"}],
  "resources_required": "…", "materials_required": "…",
  "flags": {"human","animal","hazard","personal_data": ["命中词"]},
  "metrics": "见 §4", "findings": []
}
```

quantity 结构：`{"value":<基准单位数值>, "raw":"原文", "unit":"原单位", "dim":"time|volume|mass|money|count|temp|pct|length|unknown", "mixed":bool, "assumed_unit":bool}`。单位统一：time→min，volume→µL，mass→mg，money→CNY。

## 3. registry 资源条目

```json
{"id","name","kind":"equipment|material|service",
 "status":"available|outsource|purchase|unavailable|unknown",
 "status_raw":"原文状态串",
 "lead_time_days": null|number, "unit_price": null|number, "price_unit": null|string,
 "capacity_hours_per_week": null|number, "booked_hours_per_week": null|number,
 "stock": null|number, "stock_unit": null|string,
 "pack_size": null|number, "pack_unit": null|string,
 "available_from": "YYYY-MM-DD|null",
 "spec": {"<原文键>":"<原文值>"},
 "contract": bool, "contract_locator": null|string,
 "locator": "case.json#…", "raw": "原文片段"}
```

`contract` 条目按 `resource_id/id/name` 合并到 `registry`，覆盖 status/lead/price/capacity/booked 等字段并保留合同 locator。状态归一：`已具备/available→available`，`外协/outsource→outsource`，`需采购/采购/quote→purchase`，`不具备/故障/停用→unavailable`。

## 4. metrics（每计划派生值）

```json
{
  "time": {"sequential_min","operator_min","critical_min","span_days",
           "missing_duration_steps","has_cycle","unresolved_deps"},
  "equipment_occ": [{"id","occupancy_min","hours","hours_per_week","registry_key","registered"}],
  "material_use": [{"id","total","dim","parts"}],
  "cost": {"items","total","budget","budget_value","overrun"},
  "samples": {"required_n","arms","declared"},
  "schedule": {"lead_items","max_lead_days"}
}
```

关键公式：`hours_per_week = occupancy_min/60 / max(span_days/7, 1)`；`利用率 = need/(capacity-booked)`；`包数 = ceil(缺量/包装规格)`；`成本 = Σ 设备小时×单价 + Σ 材料缺量×单价`（仅已标价项）。

## 5. calc 记录（calc_trace.json）

```json
{"id","label","formula","inputs":{},"value":null|number,
 "unit","method","locators":[],"notes":[]}
```

- `inputs` 只放 JSON 可序列化的数字/短串；报告中的每个数字都必须能定位到某个 calc。
- `method` 例：`sequential_upper_bound`、`critical_path`、`utilization`、`personnel_load`、`declared_prices_only`。
- 当输入不足以计算时 `value=null` 并写 `notes`，绝不补一个数。

## 6. finding 记录（审查判断）

```json
{"id","dimension":"七类之一","plan_id","subject","claim",
 "verdict":"六枚举之一","resource_state":null|string,
 "conditions":[],"evidence":[{"locator","quote","kind","scope"}],
 "calc_refs":[],"confidence":"low|medium|high","needs_expert":bool,"notes":[]}
```

`evidence.kind` 取值：`plan`（方案原文）、`resource`（资源/合同）、`spec`（参数域）、`approval`（伦理批件）、`constraint`（题目约束）、`computed`（派生值说明）。`quote` 必须逐字；负向检查（未检出某问题）可用 `scope` 说明检索范围。

## 7. 输出契约回填

`output_contract` 支持三种形态：字段名→类型/描述的映射、字段说明对象列表、嵌套对象。回填规则：

1. 展平为 `(路径, 规格)`；顶层键 = 路径第一段。
2. 按键语义路由：`case_id/status/experiment_plan/review/plan_comparison/sources/uncertainty/pending_human/change_log/…` 映射到对应生成对象。
3. 未匹配键保留 `null` 并写入 `contract_fill_report.unfilled`，此时 `status` 降为 `partial`（诚实缺口）。
4. `output_contract` 原样回显在 `result.output_contract`，`contract_fill_report.filled` 记录每个字段的来源。

## 8. Agent 复核接口（adjudication.json）

```json
{"overrides": [{"finding_id","verdict","conditions","notes","claim","confidence"}],
 "additions": [{"id","dimension","plan_id","subject","claim","verdict",
                "resource_state","conditions","evidence","calc_refs","confidence",
                "needs_expert"}]}
```

`overrides` 按 `finding_id` 覆盖；`additions` 追加新判断，缺失 `locator+quote` 时自动降级为 `unconfirmed`。

## 9. 确定性与失败语义

- 结果不含墙钟时间戳；`runtime.stages` 固定；`integrity.payload_sha256_12` 为除 integrity 外结果内容的 SHA-256 前 12 位，重复运行应一致。
- 状态语义：`input_error`（主文件不可解析）、`insufficient_evidence`（无方案）、`partial`（七类不全或契约未填满）、`ok`（七类齐全且契约已回填）。
- 写盘失败：主输出不可写时回退 `/app/work`，仍产出合法 JSON，退出码 0。
