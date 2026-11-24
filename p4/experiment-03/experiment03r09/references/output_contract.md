# 输出契约与推理规则

本文件定义 `result.json` 与 `plan.json` 的全部字段、每个关键对象的格式，以及识别参数/响应口径/搜索策略/成本权衡/风险处置的规则。Agent 逐项填写，脚本 `scripts/main.py validate` 与 `scripts/main.py check` 据此校验。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 取自 `/input/case.json`（键 `case_id`/`caseId`/`id`/`challenge_id`/`armiId` 之一），不得为空 |
| `status` | string | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，如实取值 |
| `plan` | object | 主结果体（字段见 §2） |
| `artifacts` | array | 可编辑产物列表，每项 `{type, path, description}`；必含 `plan.md` 与 `plan.json`，均真实写入 `/app/output/` |
| `sources` | array | 证据映射，每项 `{locator, quote, role?}`（见 §3） |
| `uncertainty` | object | `{unverified_values[], missing_data[], conflicts[], notes[]}` |
| `limitations` | array | 方法/数据/范围的限制（如实，不夸大） |
| `pending_human` | array | 需专家确认的项（安全/伦理/设备可用性/阈值） |
| `change_log` | array | 每步 `{time, step, note}`，首条必为"骨架落盘"，末条为收尾 |
| `errors` | array | 运行中捕获的错误（坏 JSON/坏编码等） |
| `degradations` | array | 降级处理记录（缺软件/缺数据/越界冲突） |
| `runtime` | object | `{started_at, finished_at, seconds}` |

## 2. plan 主结果体字段（plan.json 与 result.json.plan 同构）

| 字段 | 类型 | 要求 |
|---|---|---|
| `research_question` | string | 可检验的研究问题 |
| `objective` | string | 优化目标（含方向与约束语境） |
| `assumptions` | array | 方案成立的前提假设，逐条列出 |
| `variables` | array | 自变量/因变量，每项 `{name, unit?, type?}` |
| `parameters` | array | 关键参数，格式见 §4 |
| `controls` | array | 对照设置（空白/基线/阳性对照） |
| `procedures` | array | 实验步骤，可执行到"开机"粒度 |
| `resources` | array | 设备/材料/试剂，每项 `{name, range?, spec?}` |
| `response_metrics` | array | 响应指标，格式见 §5 |
| `interpretation_criteria` | array | 判读标准（成功/失败/可接受边界） |
| `search_strategy` | object | 见 §6 |
| `cost_budget` | object | 见 §7 |
| `basis` | array | 每条权衡的依据（文献/设备/历史记录） |
| `risks` | array | 风险清单（越界/冲突/不可行） |
| `exception_handling` | array | 异常处置（降级/转人工/终止） |
| `confirmations` | array | 确认项（安全/伦理/设备可用性/专家） |

## 3. sources 证据映射

每项证据必须能逐字回溯到材料，禁止凭文件名/领域标签臆测：

```json
{"locator": "materials/device.md#量程", "quote": "设备最高允许 120°C", "role": "constraint"}
```

- `locator`：材料文件 + 段落/行定位（`文件#锚点` 或 `文件:行号`）。
- `quote`：从材料中**逐字**摘录的原文片段（非转述、非缩略）。
- `role`（可选）：`constraint`/`response`/`cost`/`basis`/`conflict`。
- 同一指标出现冲突来源时两条都记录，并标 `role: conflict`，不隐藏。

## 4. parameters 对象格式

```json
{
  "name": "反应温度", "unit": "°C",
  "suggested_value": 80,
  "feasible_range": {"min": 60, "max": 90},
  "step": 5,
  "hard_bounds": {"min": 0, "max": 120, "label": "设备量程"},
  "constraint_source": {"locator": "materials/device.md#量程", "quote": "…"},
  "rationale": "选择理由"
}
```

- `suggested_value` 必须落在 `feasible_range` 内；`feasible_range` 必须落在 `hard_bounds` 内（设备量程/材料耐温/安全阈值）。
- `step > 0`；可行区间与步进要有来源，不写笼统经验区间。
- 越界冲突（如目标温度>材料耐温）是**阻断错误**：必须解决或明确降级（降低目标/换材料/转人工），不得悄悄改约束。

## 5. response_metrics 对象格式

```json
{
  "name": "转化率", "unit": "%", "measurement_method": "GC-FID",
  "direction": "maximize", "target": "≥85",
  "acceptable_lower": 80, "prediction": "历史数据线性拟合",
  "uncertainty_note": "预测 ±5 个百分点"
}
```

- `direction`：`maximize`/`minimize`/`target`。
- `prediction`：历史数据拟合 / 代理模型 / 经验规则，**必须披露**，不把预测写成实测。
- `uncertainty_note`：预测不确定度；无法量化时写"未量化，需补充数据"。

## 6. search_strategy 对象格式

```json
{
  "method": "网格+序贯", "initial_points": [80],
  "per_round": "每轮在最优值±5°C补2点",
  "stop_criteria": ["相邻两轮最优响应差<2", "预算耗尽"],
  "candidate_count": 8, "seed": 0
}
```

- 必须写明初始点怎么选、每轮补哪些点、如何用上一轮结果调整、何时停止。
- 候选点由 `python scripts/main.py doe --work /app/work --seed 0` 确定性复现（固定 seed）。

## 7. cost_budget 对象格式

```json
{
  "items": [{"name": "机时", "unit": "h", "quantity": 6, "cost": "¥600"}],
  "total": "¥600 / 6h", "time_hours": 6,
  "tradeoff_note": "温度每降10°C，接受转化率降5个百分点以省能耗"
}
```

- 机时/耗材/样品数/通量都要纳入；`tradeoff_note` 说明成本受限时**牺牲多少响应指标可接受**。

## 8. 推理规则（评分维度落地）

1. **关键参数**：只保留真正影响响应且受约束的参数；每个参数给出名称/单位/建议值/可行区间/步进/硬边界/来源。
2. **响应口径**：单位、测量方法、方向、目标、可接受下限缺一不可；预测方式与不确定度必须披露。
3. **搜索策略**：有限参数定义域下，参数少用网格，参数多或昂贵用序贯/贝叶斯；写明收敛与停止判据。
4. **成本权衡**：多目标取舍要量化"牺牲多少可接受"，不能只写"综合考虑"。
5. **风险处置**：至少覆盖无可行解、约束冲突、数据缺失、设备报错、指标矛盾五类，各给降级/转人工/终止判断。
6. **溯源**：每个关键结论给 locator + 逐字 quote；冲突不隐藏；缺失写 `pending_human` 或 `not_reported`。
7. **不编造**：禁止编造数值、来源、DOI、评测分数、运行结果。

## 9. 完成标准

- 实验人员可据此执行，或明确知道缺什么（缺什么写进 `pending_human`/`limitations`）。
- 他人能按 `change_log` + `sources` + `search_strategy` 复跑并核对候选点。
- `python scripts/main.py check /app/output/result.json --output-dir /app/output` 退出码 0 或仅警告。
