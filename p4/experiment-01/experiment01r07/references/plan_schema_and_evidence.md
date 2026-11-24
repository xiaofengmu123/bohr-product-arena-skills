# 字段语义、六要素细则与证据规范

本文件是 SKILL.md 的补充：定义 `result.json` 的字段语义、六要素可操作写法、证据定位格式、算术校验规则与 fallback 结构。

## 1. result.json 顶层字段

| 字段 | 类型 | 说明 |
|-|-|-|
| `case_id` | string | 输入标识。取 case.json 的 case_id/id/challenge_id，缺失取 task.md 的 challenge_id，仍无写 `"unknown"` 并列 `pending_human`。 |
| `output_contract` 的键 | 任意 | 逐键原样回填；缺值 `null`/`[]`，不改名、不删未知键。 |
| `status` | string | `ok` / `partial` / `insufficient_evidence` / `input_error`，如实反映完成度。 |
| `sources` | array | 已读来源清单（文件路径/页码），供独立复核。 |
| `uncertainty` | array | 不确定项：冲突双方、影响、需专家确认的替代。 |
| `pending_human` | array | 必须由人确认/补充的缺口（安全、伦理、设备、缺失数值）。 |
| `change_log` | array | `{ts, event, detail}`，每次大改动追加；重复运行一致。 |
| `errors` / `degradations` | array | 可选：读入失败、软件缺失、超大文件跳过等。 |
| `runtime` | object | 可选：`started_at`、`finished_at` 等运行记录。 |

## 2. 六要素（fallback 结构）

无 `output_contract` 时使用；有契约时把契约键作为六要素容器，子结构按同一规范填写。

```json
{
  "plan": {
    "hypothesis_and_variables": {
      "research_question": null,
      "hypotheses": [],
      "independent_variables": [],
      "dependent_variables": [],
      "confounders": []
    },
    "controls": [],
    "equipment_materials": [],
    "time_cost": {"steps": [], "total_time": null, "total_cost": null},
    "parameter_ranges": [],
    "risks_and_exceptions": []
  }
}
```

### 2.1 假设与变量
- 研究问题必须是**可检验**的陈述，而非目标复述。
- 每个变量要有：操作定义、测量方式、单位、取值范围（量程）；明确自变量、因变量、混杂因素。

### 2.2 对照组
每项至少含 `group`、`purpose`、`confounder_controlled`；覆盖空白、阴性、阳性、剂量梯度中与研究目标匹配的组合，写明每个对照排除哪类混杂。

### 2.3 设备材料
每项至少含 `item`、`model_or_spec`、`quantity`、`unit`、`notes`；标 `replaceable`（可替代）与 `irreplaceable`（不可替代）。

### 2.4 时间成本
`steps` 每项含 `name`、`duration`（数值+单位）、`parallel_with`（并行关系）、`cost`（可选）；`total_time`、`total_cost` 与步骤求和一致（见第 4 节）。

### 2.5 参数范围
每项含 `name`、`unit`、`allowed_range`（`[lo, hi]` 或 `{min, max}`）、`chosen`（选定值）、`basis`（手册/预实验/文献）、`out_of_range_action`。

### 2.6 风险与异常
每项含 `failure_mode`（污染/无信号/重复性差/样本不足）、`criterion`（判据）、`stop_point`（止损点）、`alternative`（替代路径）。

## 3. 证据规范

- 关键结论/数值带 `locator`（如 `materials/paper.md#p3` 或 PDF 页码）与逐字 `quote`。
- 同一指标两处不一致 → 两处都记并标 `source_conflict`。
- 案例内置“前瞻性资源/价格/排期合同”只标“案例给定约束（非真实报价/审批）”，不得声称真实报价或安全审批。
- 缺失写 `not_reported` 或列 `pending_human`；不虚构引用、DOI、数值、价格、排期。

## 4. 算术校验规则

`validate` 对可解析的数值执行三项检查：

1. **参数量程**：`chosen/value` 必须落在 `allowed_range`/`min`/`max` 内，越界为硬错误。
2. **预算**：`line_items`/`steps` 的成本求和不得超过 `budget`/`ceiling`，超支为硬错误。
3. **排期**：`steps` 时长之和与 `total_time` 不一致为警告（可能是并行/舍入，需人工确认）。

无法解析或字段缺失时不臆造错误，仅记录 `checks`/`warnings`。

## 5. 候选方案比较与裁剪

组织方案例会提供 3 份版本化候选方案 + 有限参数域 + 资源/价格/排期合同。比较步骤：

1. 抽取每个候选方案的参数、量程、成本、时长、样本量。
2. 对每个候选方案套用第 4 节算术校验（量程/预算/排期）。
3. 按“假设可检验 + 对照排除混杂 + 参数在量程内 + 预算/排期可行”择优或合并修订。
4. 无法由材料决定取舍处写 `uncertainty` 并给出两种可行替代、标需专家确认。

## 6. 状态判定

| 场景 | status |
|-|-|
| 材料足够、六要素齐全、算术通过 | `ok` |
| 骨架已落盘但内容未填满 | `partial` |
| 输入目录空/无可用材料 | `insufficient_evidence` |
| 读入失败但已降级继续 | `input_error` |
