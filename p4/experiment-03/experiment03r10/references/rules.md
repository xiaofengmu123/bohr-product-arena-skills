# rules：参数识别 / 响应口径 / 搜索策略 / 成本权衡 / 风险处置

这些规则指导把材料转成 plan.json；每一条最终都要落进 result.json 的对应字段并带溯源。

## 1. 参数识别与可行域

1. 只收录“真实可调”的关键参数：温度、浓度、时间、压力、流速、投料比、采样频率等；固定量写入 `controls`，不写成参数。
2. 每个参数必须给出单位、建议值、可行区间、步进；数值来源必须可追溯到设备量程/材料耐温/安全阈值/历史记录。
3. 约束分层写入 `limits`：`device`（设备量程）、`material`（材料耐温/腐蚀/寿命）、`safety`（安全阈值）；可行域 = 三者交集。
4. 交集为空（如材料耐温 < 设备下限）判为约束冲突，写入 `risks` 与 `exception_handling`，不得悄悄改约束。
5. 互斥/禁止组合写入 `exclusions`（如“高温 × 某溶剂”）。

## 2. 响应指标口径与代理建模

1. 每个指标给 `definition`/`unit`/`measurement_method`/`direction`/`target`/`acceptable_lower`。
2. 明确 `prediction_method`：历史数据拟合 / 代理模型 / 经验规则；并披露 `uncertainty`（预测不确定度，不是测量精度）。
3. 主响应与成本/时间/通量同时列为指标，避免“单看最优、实验室做不起”。

## 3. 搜索策略

1. 在网格、序贯设计、贝叶斯优化、分阶段筛选之间选一个并说明理由。
2. 写清 `initial_points`（怎么选）、`per_round`（每轮补哪些点）、`stop_criteria`（收敛/预算耗尽/风险触发/无改善）。
3. 候选点由 `python scripts/main.py doe --work /app/work --seed 0` 生成，固定种子保证可复现。

## 4. 成本与时间预算

1. `cost_budget.items` 逐项列口径（机时、耗材、样品数、通量、人工）。
2. `time_hours` 给总时数；`total` 给总量纲。
3. `tradeoff_note` 必须写明“成本受限时牺牲多少响应指标可接受”，以及多目标取舍规则。

## 5. 风险与异常处置

至少覆盖五种情况，每条给判断与动作：

| 情况 | 处置 |
|---|---|
| 无可行解 | 写 `exception_handling`，建议降级目标/换材料/终止 |
| 约束冲突（温度 vs 耐温） | 标冲突、列可选替代、转人工确认 |
| 数据缺失 | 写 `uncertainty.missing_data` 与 `limitations`，不补造 |
| 设备报错 | 降级到 backup 设备或终止该轮 |
| 指标互相矛盾 | 记录两个来源，标 `source_conflict`，转人工裁决 |

## 6. 确认项

安全、伦理、设备可用性、专家确认四类必须逐条列入 `confirmations`；未经确认的数值在
`pending_human` 标注，不得写成可执行事实。
