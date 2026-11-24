# 成本与时间核算口径（六项成本 / 五项时间）

比较选择最常出错的地方是“只算耗材单价、只算执行时间”。本文件固定口径，脚本与 Agent 共用同一套字段，避免各方案各写一套。

## 1. 成本六项

| category | 含义 | 常见遗漏 |
|---|---|---|
| `equipment_time` | 机时费（仪器小时单价 × 使用小时） | 忘记把表征排队的小时算进机时 |
| `consumables` | 耗材（试剂/耗材单价 × 用量） | 只写单价不写用量与批次 |
| `labor` | 人力（工日/工时 × 单价） | 把“自己做”当成零成本 |
| `sample_prep` | 样品准备（前处理耗材与工时折算） | 只算主实验，不算前处理 |
| `scheduling` | 排期（加急/预约费用或占位成本） | 漏掉设备档期与货期等待 |
| `rework` | 返工（失败重做概率 × 单次成本） | 风险概率不折算成成本 |

每条 `cost_line` 结构：

```json
{
  "category": "equipment_time",
  "item": "高分辨质谱机时",
  "unit": "h",
  "unit_price": 300,
  "quantity": 12,
  "one_off": false,
  "evidence": {"claim_id":"C1","claim":"质谱机时费 300 CNY/h",
               "locator":"materials/quote.md#p4","quote":"- 质谱机时费：300 CNY/小时。",
               "kind":"quote","assertion_type":"fact"}
}
```

- `one_off=true` 表示一次性投入（如新购夹具、标样、认证费），按项目摊一次；`false` 表示按次消耗。
- 脚本自动算 `amount = unit_price × quantity`，并分别汇总 `one_off_total` 与 `per_run_total`。
- Agent 读真实报价后若发现脚本算术与报价表不符，用 `cost_override` 覆盖并写 `note` + `evidence`，**不得编造数字**。

## 2. 时间五项

| part | 含义 |
|---|---|
| `sample_prep` | 样品准备时间 |
| `scheduling` | 设备排期/货期等待 |
| `execution` | 实验执行（主实验机时/操作） |
| `queue` | 表征排队时间 |
| `rework` | 返工重做时间（概率加权后的期望值） |

```json
{
  "time_parts": {"sample_prep": 6, "scheduling": 8, "execution": 40,
                 "queue": 12, "rework": 10, "unit": "h"}
}
```

脚本自动求和为 `time.total`（小时），并与时间窗硬阈值（如 120 h）比较。

## 3. 硬阈值淘汰规则

脚本按以下顺序检查并写入 `infeasible_reasons`：

1. `cost.total > budget`（预算上限）→ `over_budget(实际>上限)`
2. `time.total > deadline`（时间窗）→ `over_time_window(实际>上限)`
3. `feasibility.equipment_available == "no"` → `equipment_unavailable`
4. `comparison_frame.hard_constraints[]` 中任何 `{field, op, value}` 命中 → 记录约束名

被淘汰的方案仍留在矩阵中并标 `infeasible`，但不进入排名；`selection.rejected[]` 逐条写舍弃理由。全部不可行时 `chosen=null`，status=partial。

## 4. 权重与敏感性

- 默认权重：`goal_fit 0.25 / feasibility 0.25 / evidence 0.20 / cost 0.15 / risk 0.15`。
- 任一维度缺评分时，脚本按剩余维度重新归一化权重，不崩溃。
- 敏感性：每维权重 ±0.05 后重排名，输出 `sensitivity.robust`（扰动下排名是否不变）与逐项 `perturbations`。若 `robust=false`，选择结论需在 rationale 里说明对哪个权重敏感。

## 5. 核算示例（示意，非正式案例）

候选 P1：机时 40 h × 300 = 12,000；耗材 2,000；人力 3 工日 × 800 = 2,400；样品准备 500；排期 0；返工 10% × (12,000+2,000) = 1,400。总成本 18,300 CNY，其中按次 18,300、一次性 0；时间 6+8+40+12+10 = 76 h。预算 50,000、时间窗 120 h，均通过。

正式案例的数字必须以材料为准，上表仅说明字段如何填。
