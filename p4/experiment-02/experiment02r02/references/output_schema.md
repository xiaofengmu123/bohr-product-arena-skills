# result.json 输出契约与自检清单

`/app/output/result.json` 是唯一主结果；同目录另产 `review_report.md`（可编辑）、`arithmetic_ledger.csv`、`plan_score_matrix.csv` 供复核。所有字段除契约字段外均可缺省，但以下为硬要求。

## 1. 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 由 `case_id/id/challenge_id/caseId` 识别，失败退化为文件名派生或 `unknown` |
| `challenge_id` | string\|null | 案例 challenge id（有则填） |
| `task_title` | string\|null | 案例标题 |
| `status` | enum | `ok \| partial \| insufficient_evidence \| input_error` |
| `review_mode` | enum | `single_plan \| versioned_plans` |
| `schema_name/schema_version` | string | 本 Skill 输出 schema 标识 |
| `contract_fields` | object | `output_contract` 每个字段名 → `{status, type}`；**字段本身原样回填到顶层**（缺值给 `null`/`[]`/`{}`，不更名不删键） |
| `executive_summary` | object | `verdict`、`recommended_plan`、`blocking_count`、`unconfirmed_count`、`top_conditions`、`confidence` |
| `research_question` | object | 取方案声明的目标；未声明置 null，不代写 |
| `plan_profiles` | array | 每版：`plan_id/locator/goal/budget/deadline_days/step_count/parameter_count/resource_use_count/sample` |
| `review` | object | 七类各一个数组：`assumptions_variables/controls/equipment_materials/time_cost/parameter_ranges/risks_exceptions/ethics_safety` |
| `plan_comparison` | object | `weights/scores/ranking/recommended/sensitivity/sensitivity_flips/method` |
| `arithmetic_ledger` | array | 见第 3 节 |
| `evidence_index` | array | 去重后的全部证据片段，`eid/locator/quote/quote_verified/kind` |
| `confirmation` | object | `safety/ethics/equipment_availability/expert_confirmation` 四组确认项 |
| `sources` | array | 材料/合同/文献来源（有则填，不臆造） |
| `uncertainty` | array | `item/reason/impact/resolve` |
| `pending_human` | array | `item/who/what/order` |
| `limitations` | array | 本次未完成或不能做的事，如实说明 |
| `errors/degradations` | array | 坏 JSON、超限、缺软件、解析失败等 |
| `runtime` | object | `budget_seconds=900/budget_requests=64/stage/wall_seconds/requests`；时长与请求由平台记录，本文件不臆测 |
| `input_inventory` | array | 输入文件清单（relpath/bytes/status） |
| `artifacts` | array | 附属产物路径与类型 |
| `change_log` | array | 阶段/动作/原因 |
| `selfcheck` | object | 硬自检是否通过 + 明细 |

## 2. review 条目 schema

```json
{
  "id": "TC-v1-budget",
  "dimension": "time_cost",
  "check": "具体到方案/步骤/参数的判断句",
  "target": "plans[0]",
  "verdict": "ok | conditional | blocked | unconfirmed | missing | conflict",
  "resource_state": "仅设备材料维度：已具备|可外协|需采购|不具备|未知",
  "conditions": ["逐条可执行"],
  "evidence": [{"locator": "case.json#...", "quote": "逐字片段", "quote_verified": true, "kind": "case|ledger|agent"}],
  "confidence": "high|medium|low",
  "needs_expert": false,
  "notes": []
}
```

## 3. arithmetic_ledger 条目 schema

```json
{
  "id": "L-cost-v1",
  "kind": "cost|time|sample",
  "title": "v1 采购/使用成本",
  "expr": "p1*q1 + r2*h2 + f3",
  "terms": [{"symbol": "p1", "value": 4200, "locator": "case.json#contracts...", "quote": "\"unit_price\":4200", "unit": "CNY", "derived": false}],
  "result": 9620,
  "unit": "CNY",
  "status": "computed | not_computable",
  "recomputed": 9620,
  "recompute_ok": true,
  "assumptions": ["..."],
  "notes": []
}
```

## 4. 状态判定口径

- `insufficient_evidence`：无 case.json、或无法识别任何候选方案。
- `input_error`：case.json 存在但无法解析。
- `ok`：七类维度均有条目、可复算台账 ≥ 3、逐字命中证据 ≥ 5。
- `partial`：其余情况（诚实保留已做部分，`limitations` 写清缺口）。

## 5. 自检清单（交付前逐条勾选）

**实际效果**

- [ ] 七类约束各至少一条判断，且无"方案没写当满足"
- [ ] 每条 `ok/conditional/blocked/conflict` 都有 locator + 逐字 quote（或 ledger 引用）
- [ ] 每个数值结论有 `expr + terms`，且 `recompute_ok=true`
- [ ] 明确指出方案缺失信息与自相矛盾处（`missing/conflict` 与 `pending_human`）

**使用增益 / 创新增益**

- [ ] 同方案同口径复跑可比较：判断来自原文而非记忆；`arithmetic_ledger` 可复算
- [ ] 方案×合同交叉核验、参数域校验、版本排序全部由确定性脚本完成，非口头估计

**可靠体验**

- [ ] `result.json` 合法，含 `case_id`、`status`、契约字段、`sources/uncertainty/change_log`
- [ ] 空输入 / 坏 JSON / 缺页 / 超长文档 / 伦理未定 / 方案矛盾 / 二进制材料均有明确处置
- [ ] 重复运行结构一致（除 `runtime` 与 `artifacts` 绝对路径外逐字节一致）

**生态贡献**

- [ ] `SKILL.md` 写清用途、输入输出、失败处理、阶段预算；脚本与参考文件真实存在、相对路径可执行
- [ ] `review_report.md`/CSV 可编辑；检查项库与规则可复用

## 6. 验证命令

```bash
python3 scripts/main.py --stage verify --output /app/output   # 对已产出结果跑不变量
python3 scripts/selftest.py                                  # 合成小案例自检（非正式答案）
```
