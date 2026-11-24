# 审查报告模板（可复用，供 /app/output/review_report.md）

本模板给出一份**可编辑**的 `review_report.md` 结构与配套的 `result.json` 审查项写法，Agent 在 S5 阶段据此生成报告，他人可照模板复跑或用模板做内部评审。示例片段一律标注"示例"，不冒充正式案例结果。

## 1. review_report.md 结构

```markdown
# 实验方案可行性审查报告

- case_id: {{case_id}}
- 版本: {{plan_version_or_方案标识}}
- 状态: {{status}}
- 生成步骤: {{skeleton→S1→S5 的 change_log 摘要}}

## 0. 总体结论
{{overall_conclusion.summary}}
- 判定: {{overall_conclusion.verdict}}
- 需满足的条件: {{overall_conclusion.conditions}}
- 阻塞项: {{overall_conclusion.blocks}}

## 1. 实验方案摘要
- 研究问题: {{experiment_plan.research_question}}
- 假设: {{experiment_plan.assumptions}}
- 变量: {{experiment_plan.variables}}
- 对照: {{experiment_plan.controls}}
- 步骤: {{experiment_plan.steps}}
- 参数: {{experiment_plan.parameters}}
- 资源: {{experiment_plan.resources}}
- 判读标准: {{experiment_plan.interpretation_criteria}}

## 2. 七类约束逐条审查
### 2.1 假设与变量
- [{{id}}] {{claim}} — **{{verdict}}**（confidence={{confidence}}）
  - 证据: {{locator}} «{{quote}}»
  - 条件/缺口: {{conditions|notes}}
（2.2–2.7 同理覆盖 controls / equipment_materials / time_cost /
 parameter_ranges / risks_exceptions / ethics_safety）

## 3. 依据与风险
- 依据: {{evidence_and_risks.evidence}}
- 风险: {{evidence_and_risks.risks}}
- 异常处置: {{evidence_and_risks.exception_handling}}

## 4. 确认项
- 安全: {{confirmation.safety}}
- 伦理: {{confirmation.ethics}}
- 设备可用性: {{confirmation.equipment_availability}}
- 专家确认: {{confirmation.expert_confirmation}}

## 5. 不确定性与待人工
- 不确定性: {{uncertainty}}
- 待人工: {{pending_human}}
- 降级/局限: {{degradations}} / {{limitations}}
```

## 2. 审查项 JSON 模板（写入 `result.json` 的 `review.*`）

```json
{
  "id": "eq-1",
  "dimension": "equipment_materials",
  "claim": "所需离心机型号在本单位清单中不存在",
  "verdict": "conditional",
  "resource_state": "需采购",
  "conditions": ["确认采购到货周期 ≤ 项目起始日", "确认替代机型可满足转速/容量"],
  "computation": "3周×40h=120h 货期 > 剩余窗口 96h",
  "evidence": [
    {"locator": "case.json#plans.0.steps[2]", "quote": "原文逐字片段", "kind": "plan"},
    {"locator": "case.json#resources.equipment", "quote": "原文逐字片段", "kind": "contract"}
  ],
  "confidence": "medium",
  "needs_expert": false,
  "notes": []
}
```

字段约束：
- `verdict` ∈ `ok | conditional | blocked | unconfirmed | missing | conflict`。
- `resource_state` 仅 `equipment_materials` 维度必填：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
- `ok/conditional/blocked/conflict` 必须带 `evidence`（`locator` + 非空 `quote`）。
- `conditional` 必须带非空 `conditions`；`time_cost`/`parameter_ranges` 建议带 `computation`。

## 3. result.json 顶层字段核对表（交付前逐项确认）

| 字段 | 必须 | 说明 |
|---|---|---|
| `case_id` | 是 | 与输入一致；缺失填 `unknown` |
| 题目 `output_contract` 全部键 | 是 | 原样回填，不更名/不删键，缺值给 `null`/`[]` |
| `status` | 是 | `ok/partial/insufficient_evidence/input_error` |
| `overall_conclusion` | 是 | verdict/summary/conditions/blocks/notes |
| `experiment_plan` | 是 | 8 个子字段 |
| `review` | 是 | 七类，每类数组 |
| `plan_comparison` | 否 | 多版方案时填 |
| `evidence_and_risks` | 是 | evidence/risks/exception_handling |
| `confirmation` | 是 | safety/ethics/equipment_availability/expert_confirmation |
| `sources/uncertainty/pending_human/limitations` | 是 | 诚实标注 |
| `errors/degradations/change_log/runtime/artifacts` | 是 | 运行记录 |

## 4. 已填审查项示例（示例，非真实案例结果）

```json
{
  "id": "tm-2",
  "dimension": "time_cost",
  "claim": "总工期约 122.5 小时，超出 2 周（80 工作小时）窗口",
  "verdict": "blocked",
  "computation": "0.5h+2h+3×40h=122.5h > 80h",
  "evidence": [
    {"locator": "case.json#plans.0.steps", "quote": "原文逐字片段", "kind": "plan"}
  ],
  "confidence": "high",
  "needs_expert": false,
  "notes": ["若采购货期可压缩至 1 周则降为 conditional"]
}
```

## 5. 复跑说明

1. 用 `python3 scripts/main.py --input /input --output /app/output --work /app/work` 重建骨架与输入清单。
2. 按本模板在 `result.json` 的 `review.*` 落七类审查项，`computation` 写算式。
3. 运行 `python3 scripts/selfcheck.py --input /input --output /app/output`，出现 FAIL 则按提示修正。
4. 以最终 `result.json` 同内容填写 `review_report.md`，保证每条判断都有 `locator + quote`，可被独立复核。
