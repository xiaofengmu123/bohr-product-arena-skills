# 评分维度对齐与 analysis.json 规范（rubric-mapping）

本文件是 Agent 与脚本之间唯一的接口规范：Agent 写 `/app/work/analysis.json`，脚本据此做算术、淘汰、评分、矩阵与产物。所有示例值均标注"示例"，不是正式案例答案。

## 1. 评分维度 → 流程 → 产出 → 自检项

| 评分维度 | 分值 | 流程步骤 | 产出字段 | 自检项（逐条可勾选） |
|---|---|---|---|---|
| 实际效果 | 40 | 第 2–4 步 | `comparison_matrix`、`selection`、`candidates`、`sources` | 所有候选填同一七类字段矩阵；选择有 chosen+ranking+rationale+rejected；每个数字挂 locator+quote |
| 使用增益 | 25 | 第 3 步（统一模板） | `seven_fields`、`cost`/`time` 模板 | 一套模板避免各写各的；脚本求和而非手算；重跑 assemble 即可复核 |
| 创新增益 | 15 | 第 4 步（脚本自动） | `selection.decision_rules`、`selection.sensitivity`、`evidence_ledger` | 硬阈值确实执行；权重 ±0.05 敏感性有结果；证据去重 |
| 可靠体验 | 10 | 第 1/4/5 步 | `status`、`errors`、`degradations`、`pending_human` | 空输入/坏 JSON 退出码 0；重复运行一致；不可行如实标 partial |
| 生态贡献 | 10 | 全流程 | `SKILL.md`、本文件、`checklist.md`、产物 | 他人可独立复跑；CSV/SVG/MD/PDF 齐备可打开 |

脚本把上述映射写进 `result.json` 的 `rubric_map`，复核者可逐条核对。

## 2. analysis.json 顶层 schema

```json
{
  "case_id": "…",
  "research_question": {"testable_question":"…","variables":{"independent":[],"dependent":[],"controlled":[]},"hypothesis":"…"},
  "comparison_frame": {"objectives":[],"hard_constraints":[],"criteria":["goal_fit","risk","cost","feasibility","evidence"],"weights":{"goal_fit":0.25,"feasibility":0.25,"evidence":0.20,"cost":0.15,"risk":0.15}},
  "candidates": [],
  "selection": {"chosen":"P2","ranking":["P2","P1","P3"],"rationale":"…","rejected":[{"plan_id":"P1","reason":"…"}],"sensitivity":{}},
  "confirmations": [], "uncertainty": [], "sources": [], "contract_fills": {},
  "change_log": [], "errors": [], "degradations": [], "pending_human": [], "limitations": []
}
```

- `case_id` 必须与 case_pack.json 一致；脚本以 analysis 为准，缺失时退回 case_pack。
- `criteria` 固定五维；`weights` 只认五维键，缺失维度由脚本重新归一化，全缺则用默认权重。
- `contract_fills`：`case.json` 的 `output_contract` 中无法自动计算的键，Agent 在此填值；脚本对未映射键回填 null。

## 3. 单个候选方案 schema

```json
{
  "plan_id": "P1",
  "name": "…",
  "version": "v1",
  "seven_fields": {
    "assumptions": "…", "variables": "…", "controls": "…",
    "steps": "…", "parameters": "…", "resources": "…", "interpretation_criteria": "…"
  },
  "dimension_ratings": {"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
  "feasibility": {"equipment_available":"yes|no|partial|unknown","lead_time_ok":"yes|no|unknown","notes":"…"},
  "risks": [{"trigger":"…","consequence":"…","magnitude":"…","mitigation":"…","fallback":"…"}],
  "evidence": [{"claim":"…","locator":"materials/xxx.md#p3","quote":"逐字引用","kind":"literature|manual|quote|pilot|log"}],
  "infeasible_reasons": [],
  "cost": {"components":{"machine_hours":0,"consumables":0,"labor":0,"sample_prep":0,"scheduling":0,"rework":0},"one_time":[],"currency":"CNY","note":"…","evidence":"…","method":"computed"},
  "time": {"components":{"preparation":0,"scheduling":0,"execution":0,"rework":0},"unit":"h","note":"…","evidence":"…","method":"computed"},
  "cost_override": null,
  "time_override": null
}
```

### 七类字段（实际效果的核心）

| 字段 | 含义 | 完成标准 |
|---|---|---|
| assumptions | 方案成立的前提假设 | 逐条可被复核 |
| variables | 自变量/因变量/控制变量 | 可检验、可测量 |
| controls | 对照与对照组 | 能区分处理效应 |
| steps | 操作步骤 | 实验人员可执行 |
| parameters | 参数及范围 | 注明来源（设备极限/文献/经验） |
| resources | 设备、耗材、人员、场地 | 与"本实验室现在可做"核对 |
| interpretation_criteria | 判读标准 | 明确成功/失败阈值 |

### 五维评分（0–5，5 最优）

- `goal_fit` 目标契合：方案是否直接回答研究问题；
- `risk` 风险：越低越优（5 分 = 风险最低）；
- `cost` 成本：越低越优；
- `feasibility` 可行性：设备/货期/技能是否齐备；
- `evidence` 证据：参数与结论有多少来源支撑。

评分必须与 `evidence` 对应：高分要有 locator+quote，不能只靠语气。

### 成本与时间（使用增益的核心）

- 成本六项：`machine_hours`（机时）、`consumables`（耗材）、`labor`（人力）、`sample_prep`（样品准备）、`scheduling`（排期）、`rework`（返工）。`one_time` 列一次性投入项名，其余视为按次消耗。
- 时间四项：`preparation`（准备）、`scheduling`（排期）、`execution`（执行）、`rework`（返工），单位 `h`。
- 脚本默认 `method:"computed"` 时对 components 求和；Agent 读真实报价后可用 `cost_override`/`time_override` 覆盖，`method` 自动标 `override`。
- 材料不足以核算时，Agent 显式写 `"method":"not_computable"`；脚本不强行给数，标 unresolved 并挂 pending_human。

## 4. 证据溯源（硬要求）

每个关键结论/数值/参数范围都要挂 `evidence`，`quote` 必须是材料里的**逐字引用**：

- `locator`：`materials/<文件>.md#p3` 或 `case.json` 路径 + 段落号；
- `quote`：逐字片段，不长于一句话或一个数值；
- `kind`：`literature`（文献）、`manual`（设备手册）、`quote`（报价/合同）、`pilot`（预实验）、`log`（历史日志）。

材料没给的写 `pending_human` 或 `not_reported`，禁止编造 DOI、数值、引用。冲突信息两条都记录并标 `source_conflict`。

## 5. 硬阈值与选择（创新增益的核心）

脚本按固定规则淘汰（写入 `selection.decision_rules`）：

1. `feasibility.equipment_available == "no"` → 不可行；
2. `feasibility.lead_time_ok == "no"` → 不可行；
3. `cost.total > budget` → 不可行；
4. `time.total > time_window` → 不可行；
5. `method == "not_computable"` → 不自动淘汰，unresolved + pending_human。

敏感性：每个维度权重 ±0.05 扰动后重新归一化、重排名；`selection.sensitivity.stable=false` 表示第一名会翻转，需在 rationale 说明。

## 6. 最小示例（示例，非正式案例）

```json
{
  "case_id": "demo-001",
  "research_question": {"testable_question":"示例：哪种纯化路线在成本与纯度约束下最优？","variables":{"independent":["纯化方法"],"dependent":["纯度"],"controlled":["原料批次"]},"hypothesis":"示例"},
  "candidates": [{
    "plan_id":"P1","name":"示例：柱层析","version":"v1",
    "seven_fields":{"assumptions":"示例","variables":"示例","controls":"示例","steps":"示例","parameters":"示例","resources":"示例","interpretation_criteria":"示例"},
    "dimension_ratings":{"goal_fit":4,"risk":3,"cost":3,"feasibility":4,"evidence":3},
    "feasibility":{"equipment_available":"yes","lead_time_ok":"yes","notes":"示例"},
    "risks":[],"evidence":[{"claim":"示例","locator":"demo/README.md#L1","quote":"示例引用","kind":"literature"}],
    "cost":{"components":{"machine_hours":400,"consumables":240,"labor":80,"sample_prep":50,"scheduling":30,"rework":20},"currency":"CNY","method":"computed"},
    "time":{"components":{"preparation":4,"scheduling":2,"execution":16,"rework":2},"unit":"h","method":"computed"},
    "cost_override":null,"time_override":null
  }],
  "selection":{"chosen":"P1","ranking":["P1"],"rationale":"示例","rejected":[],"sensitivity":{}}
}
```
