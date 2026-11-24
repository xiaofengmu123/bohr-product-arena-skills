# 字段语义、证据与 basis 规范（fallback 结构）

本文档定义 `result.json` 的稳定字段语义、证据溯源规则、不确定性分类与可复用模板。运行时代码以 `scripts/core_impl.py` 为准；本文档是给 Agent 与复核人读的契约解释。

## 1. result.json 顶层结构

| 字段 | 类型 | 含义 | 是否必需 |
|-|-|-|-|
| `case_id` | string | 案例标识；来源优先级 case.json.case_id → case.json.id → case.json.challenge_id → task.md 的 challenge_id → `"unknown"` | 是 |
| `<output_contract 键>` | 视契约 | 题目输出契约的全部字段，逐键原样回填，不改名、不删未知键；缺值 `null`/`[]` | 是（有契约时） |
| `plan` | object | fallback 主结果：六要素（无契约时） | fallback 时是 |
| `evidence_and_risks` | object | 依据列表 + 风险列表 | fallback 时是 |
| `confirmations` | array | 安全/伦理/设备可用性/专家确认项 | fallback 时是 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error` | 是 |
| `sources` | array | 来源清单（id/kind/locator/title/note） | 是 |
| `uncertainty` | array | 不确定性项 | 是 |
| `pending_human` | array | 需人确认项 | 是 |
| `limitations` | array | 范围与缺口限制 | 是 |
| `degradations` | array | 软/硬件降级记录 | 可选 |
| `errors` | array | 运行时错误记录 | 可选 |
| `change_log` | array | `{seq, event, detail}` 增量记录 | 是 |
| `runtime` | object | `{engine, version}`；墙钟由平台记录，内容不含时间戳 | 可选 |

`status` 语义（务必诚实，validate 会交叉校验）：

- `ok`：六要素全部非空、无未决 `pending_human`、依据可溯源。只有确认项已解决或明确不适用时才用。
- `partial`：已产出可读方案但有缺口（六要素未齐、某些数据 `not_reported`、存在 pending_human）。这是"诚实的不完整"，不是失败。
- `insufficient_evidence`：材料不足以形成方案，只能给出缺口清单。
- `input_error`：输入坏 JSON / 缺 case.json 且无法判定。

## 2. fallback 输出契约与六要素子结构

无 `output_contract` 时使用 `{"plan":{}, "evidence_and_risks":{}, "confirmations":{}}`。`plan` 内部固定六个键（validate 按此检查覆盖度）：

### 2.1 hypothesis_and_variables

```json
{
  "hypothesis": "可检验陈述（含方向与预期效应）",
  "independent_variables": [
    {"name":"", "operational_definition":"", "unit":"", "levels_or_range":"", "measurement_method":"", "basis":""}
  ],
  "dependent_variables": [
    {"name":"", "operational_definition":"", "unit":"", "measurement_method":"", "basis":""}
  ],
  "confounders": [
    {"name":"", "how_to_control":"", "basis":""}
  ],
  "operational_definitions": ["概念→可测操作的映射"],
  "measurement_methods": ["方法与单位"]
}
```

判据：假设可证伪；每个变量有操作定义、单位、量程/水平；混杂因素逐个给出控制方式。

### 2.2 controls

```json
{
  "groups": [
    {"id":"", "type":"blank|positive|negative|dose_gradient", "purpose":"",
     "confounder_addressed":"该对照排除哪类混杂", "n_per_group":0,
     "justification":"", "basis":""}
  ],
  "randomization":"", "blinding":"", "replicates":""
}
```

判据：至少覆盖空白 + 阳性/阴性/梯度中的一类；每个对照写明排除的混杂。禁止只有空白组。

### 2.3 equipment_materials

```json
{
  "items": [
    {"name":"", "spec_or_model":"", "quantity":0, "unit":"", "purpose":"",
     "essential":true, "alternative":"", "availability":"", "basis":""}
  ],
  "notes":""
}
```

判据：步骤绑定到型号/量程/规格；标可替代项与不可替代项；无型号信息写 `not_reported`，不得编型号。

### 2.4 time_cost

```json
{
  "steps": [
    {"id":"", "name":"", "duration_hours":0, "equipment":"", "materials":"",
     "parallel_group":"", "depends_on":[]}
  ],
  "sample_count":"", "total_hours":0, "budget":"",
  "budget_items":[{"item":"", "amount":0, "currency":"", "basis":""}],
  "schedule_conflicts":[]
}
```

判据：步骤时长、并行关系、样本量、预算可排期可报价；`total_hours` 与步骤之和一致（validate 校验）；`basis` 用 `case_given`（案例给定前瞻性合同，非真实报价）或 `computed`/`not_reported`。

### 2.5 parameter_ranges

```json
{
  "parameters": [
    {"name":"", "unit":"", "allowed_min":0, "allowed_max":0,
     "equipment_range_min":0, "equipment_range_max":0,
     "basis":"", "out_of_range_handling":""}
  ]
}
```

判据：关键参数有单位与允许区间；`basis` 说明来自设备手册/预实验/文献/案例约束；区间在设备量程内（validate 校验）；超出区间的处置写明。

### 2.6 risks_and_anomalies

```json
{
  "risks": [
    {"failure_mode":"", "detection":"", "threshold":"", "stop_point":"",
     "alternative_path":"", "probability_estimate":"", "basis":""}
  ],
  "notes":""
}
```

判据：至少覆盖污染/无信号/重复性差/样本不足中的相关项；每个风险有失败模式、判据、止损点、替代路径。

## 3. evidence_and_risks 与 confirmations

`evidence_and_risks.evidence[]` 每条至少：

```json
{"id":"", "claim":"被支撑的结论", "locator":"materials/paper.md#p3 或 PDF 页码",
 "quote":"材料中的逐字原句", "source_id":"", "confidence":"high|medium|low"}
```

`locator` 与 `quote` 二者必填（validate 硬性检查）；冲突不隐藏，同一指标两来源不一致时两条都记并标 `source_conflict`。

`evidence_and_risks.risks[]` 复述 2.6 的字段；`confirmations[]` 每项：

```json
{"id":"", "item":"安全/伦理/设备可用性/专家确认", "type":"safety|ethics|equipment|expert",
 "status":"pending|resolved|not_applicable", "owner":"", "note":""}
```

未确认一律 `status:"pending"`，不得写成可执行事实。

## 4. basis 词汇（不编造闸门）

每个"声明项"（参数、设备/耗材、预算项）必须带 `basis`，取值限于：

| basis | 含义 | 是否需要 locator |
|-|-|-|
| `case_given` | 案例给定的前瞻性资源/价格/排期合同，非真实报价/审批 | 否 |
| `computed` | 由案例数值算术推得（如样本量×单价） | 否（但算式应可复核） |
| `assumption` | 显式假设 | 否 |
| `not_reported` | 材料未报告，空缺诚实标注 | 否 |
| `pending_human` | 需人确认 | 否 |
| `literature` / `device_manual` | 文献/设备手册依据 | 是（需 locator/source_id） |

validate 把"无 basis"与"basis 非法"判为 error。这就是把"缺数据不编造"从口号变成可执行闸门。

## 5. uncertainty 与 limitations 分类

`uncertainty[]` 每项：

```json
{"id":"", "statement":"不确定的是什么", "kind":"epistemic|aleatoric|conflict|not_reported",
 "affects":"影响哪个结论/字段", "severity":"high|medium|low",
 "resolution":"如何消除（再测/文献/专家）", "pending_human":true}
```

- `epistemic`：可通过更多证据消除的认知不确定（如缺预实验）。
- `aleatoric`：随机波动（生物学重复、批次差）。
- `conflict`：两处来源冲突（配合 evidence 的 `source_conflict`）。
- `not_reported`：材料未报。

`limitations[]` 每项：`{id, scope, limit, reason}`，写明方案适用范围边界与为什么有限制。

## 6. 可复用模板

写 `plan.md` 时按 2.1–2.6 六节展开，每节末尾附"依据与 basis"子段；参数表建议直接用 2.5 的字段列成表格。这样 `result.json` 与 `plan.md` 字段一一对应，便于 validate 与人工复核。

## 7. 确定性约定

- JSON 键序固定（代码按固定顺序构造）；无随机数。
- 内容不含墙钟时间戳；`change_log` 用整数 `seq` 递增。
- 同一输入两次 scaffold 产出字节一致（selftest 校验）。
