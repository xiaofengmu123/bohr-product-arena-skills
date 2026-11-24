# plan.ir.json：结构化设计中间表示规范

## 1. 定位

`plan.ir.json` 是本 Skill 的唯一事实源（single source of truth）。Agent 不直接写最终结果，而是把案例材料转译成带稳定 ID 的节点图；`build` 对节点做结构、算术与证据校验后，渲染 `result.json` 与 `plan.md`。这一设计保证：

- 正文与结构化结果不会漂移（都从同一 IR 渲染）；
- 每条结论可定位到节点 ID → 证据 ID → 原文 locator+quote；
- 算术（预算、排期、参数区间、候选可行性）可机器复算。

## 2. 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `ir_version` | string | 固定 `"1.0"` |
| `case_id` | string | 与 result.json 一致 |
| `state` | string | `skeleton`/`built`（脚本维护） |
| `goal` | object | `statement`（研究目标原句）、`testable_question`、`success_criteria[]` |
| `assumptions` | A[] | `statement` + `impact_if_wrong` |
| `hypotheses` … `evidence` | 节点列表 | 见第 3 节 |
| `selection` | object | `chosen`、`rationale`、`selection_source` |
| `constraints` | object | `budget_cap`、`time_window_minutes`、`currency`、`parameter_domains`、`unavailable_resources` |
| `budget_schedule` | object | `sample_count`、`replicates`、`total_runs`、`wall_clock_minutes`、`cost_items[]`、`cost_total`、`currency` |
| `conflicts` | X[] | 来源冲突（可显式声明；同指标冲突取值也会自动检出） |
| `intake` | object | 扫描得到的材料索引与原始约束键（Agent 转译成节点的入口） |

节点列表键与 ID 前缀：`hypotheses`=H、`variables`=V、`controls`=C、`steps`=S、`parameters`=P、`resources`=R、`readout_criteria`=RC、`risks`=RSK、`confirmations`=CF、`candidates`=CAND、`evidence`=E、`assumptions`=A。缺失 ID 时脚本按前缀自动补并保证唯一。

## 3. 节点字段（必需 vs 建议）

| 节点 | 必需 | 建议 |
|-|-|-|
| hypothesis | `statement`、`falsifiable_by` | `status`、`evidence[]` |
| variable | `role`(independent/dependent/controlled/covariate)、`operational_definition`、`measurement.method`、`measurement.unit` | `range[lo,hi]` 或 `levels[]`、`instrument`、`evidence[]` |
| control | `type`(blank/positive/negative/vehicle/sham/dose_gradient/technical/biological)、`excludes_confounder` | `purpose`、`expected_readout`、`evidence[]` |
| step | `duration_min`(>0)、`acceptance`、设备或材料或 method 之一 | `depends_on[]`、`parallel_group`、`parameters[]` |
| parameter | `value`、`unit`、`allowed[lo,hi]`、`source`(manual/preliminary/literature/organizer_contract/vendor) | `evidence[]` |
| resource | `kind`(equipment/consumable/reagent/software/personnel/facility)、`spec` | `capacity`/`range`、`substitutable` |
| readout_criteria | `metric`、`threshold`、`decision` | `linked_variable[]` |
| risk | `failure_mode`、`detection_criterion`、`fallback_path` | `stop_loss`、`mitigation`、`linked_steps[]` |
| confirmation | `category`(safety/ethics/equipment_availability/expert/resource/data/budget)、`item`、`status`(pending/confirmed/not_applicable/unknown) | `blocking` |
| candidate | `id`、`summary`、`parameter_choices{}`、`cost_total`、`wall_clock_minutes` | `raw`/`source` |
| evidence | `locator`、`quote`(逐字) | `supports[]`、`metric`、`value` |

## 4. 证据规范

- `locator` 形如 `materials/paper.md#p3` 或 `materials/paper.pdf#p4`：文件相对路径 + 可选页锚点；PDF 按页切分（`pdftotext -layout` 后按换页符拆分），页锚点回查对应页文本。
- `quote` 必须是原文的逐字片段。构建时按三级回查：
  1. exact：原始子串；
  2. normalized：空白归一化后子串；
  3. compact：去除全部空白后子串（兼容 CJK PDF 逐字加空的情况）。
- 回查结果写入 `checks.json` 与 `result.json.checks_summary`：`verified` / `not_found` / `source_missing` / `incomplete`。`not_found` 是错误，依据降级为假设，不冒充已核实。
- 同一 `metric` 出现两个不同 `value` 自动标 `source_conflict`；两处都保留并请专家裁决，不静默取一。

## 5. 算术公式（build 自动复算）

- 关键路径：对 `steps.depends_on` 做拓扑排序，无环时最长路径时长 = `Σ duration`；有环报 `step_cycle`。无依赖但有 `parallel_group` 时按“各组最长时间求和”。
- 预算：`cost_total = Σ(cost_items[i].qty × unit_cost)`；与声明值差超过 `max(0.01, |total|×0.5%+0.01)` 报 `budget_sum_mismatch`；超过 `constraints.budget_cap` 报 `budget_cap_exceeded`。
- 运行数：`total_runs` 应与 `sample_count × replicates` 一致；样本量 <3 且无 `sample_size_justification` 记警告。
- 参数区间：`value ∈ [allowed.lo, allowed.hi]`（或用 `parameter_domains`），超出报 `parameter_out_of_range`。
- 时间窗：推算时长 > `time_window_minutes` 报 `time_window_exceeded`。
- 候选可行性：候选的 `parameter_choices` 逐个查区间，`cost_total` 对预算、`wall_clock_minutes` 对时间窗；三项都通过为 feasible，缺约束为 unknown，任一失败为 infeasible 并列出拒绝原因。选中的候选必须 feasible（unknown 记警告）。

## 6. output_contract 映射

- case.json 顶层（或嵌套）的 `output_contract`/`outputContract` 逐键回填，不改名、不删未知键、缺值 `null`/`[]`。
- 键名按末级键做语义映射：`plan`/`design`/`protocol`→完整方案包；`hypotheses|variables|controls|steps|parameters|resources|risks|evidence|confirmations|assumptions|readout`→对应节点列表；`candidate|comparison`→候选比较包；`budget|schedule|cost`→预算排期；`checks`→校验包；`summary`→摘要包。
- 未识别键填 `null` 并记入 `uncertainty`（`contract_unmapped` 语义），完整内容仍在 `plan.ir.json` 与 `output_contract` 镜像块，不丢信息。
- 无契约时使用 fallback：`{"plan": {}, "evidence_and_risks": {}, "confirmations": {}}`，与题面“实验方案 / 依据与风险 / 确认项”一致。

## 7. status 判定

| 条件 | status |
|-|-|
| 输入空，或 case.json 无效且无其他材料 | `insufficient_evidence` |
| 无任何科学节点 | `insufficient_evidence` |
| 存在 error 级检查 | `partial` |
| 存在阻塞的 pending/unknown 确认项 | `partial` |
| 存在 warning 级检查 | `partial` |
| 以上皆否 | `ok` |

`partial` 结果必须在 `limitations` 写明缺口，不得冒充完整方案。

## 8. 一个最小合法 IR 片段（示意，非正式案例）

```json
{"ir_version": "1.0", "case_id": "demo-001",
 "goal": {"statement": "评估某抑制剂对某细胞系增殖的影响",
          "testable_question": "处理 72 h 后活细胞数是否随抑制剂浓度下降？",
          "success_criteria": ["剂量-效应曲线 R²≥0.95"]},
 "hypotheses": [{"id": "H1", "statement": "抑制剂以剂量依赖方式降低活细胞数",
                  "falsifiable_by": "最高剂量活细胞数不显著低于溶剂对照则否定"}],
 "variables": [{"id": "V1", "role": "independent", "name": "抑制剂浓度",
                 "operational_definition": "培养基中抑制剂的终浓度",
                 "measurement": {"method": "连续稀释", "instrument": "移液器", "unit": "µM"},
                 "levels": [0, 0.1, 1, 10]}],
 "controls": [{"id": "C1", "type": "vehicle", "purpose": "扣除溶剂效应",
                "excludes_confounder": "溶剂毒性"}],
 "steps": [{"id": "S1", "name": "种板", "depends_on": [], "duration_min": 60,
             "equipment": ["R1"], "materials": ["M1"], "parameters": [],
             "acceptance": "贴壁后汇合度 60–80%"}],
 "parameters": [{"id": "P1", "name": "孵育时长", "value": 72, "unit": "h",
                  "allowed": [48, 96], "source": "literature"}],
 "resources": [{"id": "R1", "kind": "equipment", "name": "细胞培养箱",
                 "spec": "37 °C, 5% CO2", "substitutable": true}],
 "readout_criteria": [{"id": "RC1", "metric": "活细胞数", "threshold": "IC50 可估计",
                        "decision": "支持 H1", "linked_variable": ["V1"]}],
 "risks": [{"id": "RSK1", "failure_mode": "支原体污染", "detection_criterion": "PCR 阳性",
             "stop_loss": "整板废弃重做", "fallback_path": "更换冻存批并复测"}],
 "confirmations": [{"id": "CF1", "category": "ethics", "item": "细胞系来源伦理审批",
                     "status": "pending", "blocking": true}],
 "budget_schedule": {"sample_count": 3, "replicates": 3, "total_runs": 9,
                      "wall_clock_minutes": 4320, "cost_items": [], "cost_total": null,
                      "currency": "CNY"},
 "evidence": [{"id": "E1", "locator": "materials/paper.md",
                "quote": "该抑制剂在 10 µM 下使活细胞数下降 50%。"}],
 "selection": {"chosen": null, "rationale": null, "selection_source": null}}
```
