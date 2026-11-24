# 输出契约：plan.json 与 result.json 字段定义

本文件定义 `/app/work/plan.json`（工作中间产物，由 Agent 增量填充）与
`/app/output/result.json`（最终交付）的字段。所有字段缺省值为 `null` / `[]`，
不得改名、不得删除未知键；输入 `output_contract` 若存在，须原样回填全部键。

## 1. 状态枚举 `status`

| 值 | 含义 | 触发条件示例 |
|---|---|---|
| `ok` | 交付完整、可执行、可复核 | 参数可行域、响应指标、搜索策略、成本、风险全部落地 |
| `partial` | 交付部分完成，已标注缺口 | 阶段 0 骨架；或某些参数/成本尚未定稿 |
| `insufficient_evidence` | 材料不足以确定方案 | 缺设备量程/材料耐温/安全阈值或历史记录 |
| `input_error` | 输入不可读或不可用 | 全部输入文件损坏、编码不可恢复 |

`status` 必须如实：材料不足就 `insufficient_evidence`，不得编造数值或结论。

## 2. plan.json（工作中间产物）

顶层键：

| 键 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`，缺省用文件名 |
| `title` | string | 方案标题 |
| `research_question` | string | 由研究目标转成的可检验问题 |
| `objective` | string | 明确目标（含方向与目标值） |
| `assumptions` | list[string] | 假设，逐条可复核 |
| `variables` | list[object] | 变量：`{id, name, type(independent/dependent/controlled), unit}` |
| `parameters` | list[object] | 关键参数，结构见下 |
| `controls` | list[object] | 对照：`{id, name, description, purpose}` |
| `procedures` | list[string] | 步骤，编号顺序可执行 |
| `resources` | list[object] | 资源：`{name, spec, source}` |
| `response_metrics` | list[object] | 响应指标，结构见下 |
| `interpretation_criteria` | list[object] | 判读标准：`{metric, threshold, action}` |
| `search_strategy` | object | 搜索策略，结构见下 |
| `cost_budget` | object | 成本预算，结构见下 |
| `basis` | list[object] | 依据：`{claim, source_id}` |
| `risks` | list[object] | 风险：`{id, description, likelihood, impact, mitigation}` |
| `exception_handling` | list[object] | 异常处置：`{scenario, detection, action, exit_condition}` |
| `confirmations` | list[object] | 确认项：`{item, status(confirmed/pending), owner, note}` |
| `candidates` | list[object] | 版本化候选方案，结构见下 |
| `candidate_evaluation` | list[object] | 候选方案评估，结构见下 |
| `sources` | list[object] | 证据映射：`{id, locator, quote, claim, item}` |
| `uncertainty` | object | 不确定性：`{unverified_values[], missing_data[], assumptions[], prediction_uncertainty[]}` |
| `limitations` | list[string] | 限制 |
| `pending_human` | list[object] | 待人工确认：`{item, reason}` |
| `change_log` | list[object] | 变更记录：`{at, action, status, note}` |
| `errors` | list[string] | 运行错误 |
| `degradations` | list[string] | 降级记录 |

### 2.1 关键参数 `parameters[]`

```json
{
  "id": "T",
  "name": "反应温度",
  "unit": "°C",
  "value": 120,
  "min": 80,
  "max": 160,
  "step": 5,
  "device_limit": {"value": 200, "source": "materials/equipment.md#p2"},
  "material_limit": {"value": 150, "source": "materials/spec.pdf#p4"},
  "safety_limit": {"value": 180, "source": "materials/safety.md#p1"},
  "target": 140,
  "constraint_source": {"locator": "materials/equipment.md#p2", "quote": "炉温量程 25–200 °C"},
  "feasible": true,
  "notes": ""
}
```

规则：`min < max`；`step > 0`；`min <= value <= max`；`value` 不越设备量程/材料耐温/安全阈值；
`target` 若超过任一硬阈值，属于约束冲突，必须写入 risks 并降级或转人工，不得悄悄改约束。

### 2.2 响应指标 `response_metrics[]`

```json
{
  "id": "yield",
  "name": "目标产物收率",
  "unit": "%",
  "primary": true,
  "measurement_method": "HPLC 面积归一法",
  "direction": "max",
  "target": 90,
  "acceptable_lower": 80,
  "prediction_method": "historical_fit",
  "predicted_value": 88,
  "prediction_uncertainty": "±3%"
}
```

规则：`direction ∈ {max, min, target}`；`prediction_method ∈ {historical_fit, surrogate, empirical_rule}`；
预测方式与不确定度必须披露，不得把估计写成实测。

### 2.3 搜索策略 `search_strategy`

```json
{
  "method": "grid | sequential | bayesian | staged_screening",
  "initial_points": ["T=120, ratio=1.2"],
  "per_round": "每轮在响应最不确定处补点",
  "stop_criteria": ["连续两轮最优响应差 < 1%", "候选点用完预算"],
  "candidate_count": 8,
  "seed": 0
}
```

规则：写明初始点、每轮补点、收敛与停止判据；候选点必须可由 `scripts/main.py doe --seed 0` 复现。

### 2.4 成本预算 `cost_budget`

```json
{
  "currency": "CNY",
  "items": [
    {"name": "机时", "quantity": 4, "unit": "h", "unit_price": 80, "subtotal": 320}
  ],
  "total": 320,
  "budget_cap": 500,
  "time_hours": 4,
  "time_budget_hours": 8,
  "tradeoff_note": "收率每提高 1%，机时增加约 0.5 h（80 元/h）"
}
```

规则：`subtotal = quantity × unit_price`；`total = Σsubtotal`；`total <= budget_cap`；
`time_hours <= time_budget_hours`；`tradeoff_note` 必须说明"牺牲多少响应指标可接受"。

### 2.5 版本化候选方案 `candidates[]`

```json
{
  "version": "v1",
  "name": "方案一",
  "source": {"locator": "input/plans.json#v1", "quote": "原文逐字"},
  "parameters": {"T": 120, "ratio": 1.2},
  "cost_items": [{"name": "机时", "quantity": 4, "unit": "h", "unit_price": 80}],
  "predicted_metrics": {"yield": {"value": 88, "uncertainty": "±3"}},
  "notes": ""
}
```

### 2.6 候选方案评估 `candidate_evaluation[]`

```json
{
  "version": "v1",
  "feasible": true,
  "violations": [],
  "total_cost": 320,
  "currency": "CNY",
  "within_budget": true,
  "predicted_metrics": {"yield": {"value": 88, "uncertainty": "±3"}},
  "pareto_rank": 1,
  "selected": true,
  "notes": ""
}
```

由 `scripts/main.py evaluate` 生成；`rank` 填充 `pareto_rank` 与 `selected`。
`feasible = violations 为空 且 within_budget`。

## 3. result.json（最终交付）

顶层键：

| 键 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入标识 |
| `status` | string | 见第 1 节 |
| `title` | string | 方案标题 |
| `plan` | object | 主结果，含 2.x 全部内容键 |
| `candidates` | list[object] | 见 2.5 |
| `candidate_evaluation` | list[object] | 见 2.6 |
| `artifacts` | object | `{plan_md, plan_json, candidates_json?, candidate_evaluation_json?}`，路径必须真实存在 |
| `sources` | list[object] | 证据映射，每条 `{id, locator, quote, claim, item}` |
| `uncertainty` | object | 见 2 |
| `limitations` | list[string] | 限制 |
| `pending_human` | list[object] | 待人工确认 |
| `change_log` | list[object] | 变更记录，首条为"骨架落盘" |
| `errors` | list[string] | 运行错误 |
| `degradations` | list[string] | 降级记录 |
| `runtime` | object | `{started_at, finished_at, seconds}` |

`artifacts.plan_md` 与 `artifacts.plan_json` 必须与 `result.json` 路径互相对应且实际存在；
由 `scripts/main.py finalize` 生成。`plan.md` 可打开可编辑，`plan.json` 为结构化规格。
