# 规则与检查清单

本文件给出从材料到五层中间表示的抽取规则与判读标准。原则：**先读材料，再建模；每个关键值必须有 locator + 逐字 quote；不确定就标注，不编造。**

## 1. 关键参数识别与可行域（→ constraint_model.json）

1. 从设备手册、材料规格、安全阈值、历史记录抽出参数，而不是泛泛经验范围。
2. 每个参数必须能回答：名称、单位、建议值、可行区间 `[min,max]`、步进 `step`、硬边界来源（equipment/material/safety）。
3. 硬边界取各来源的**交集**：`domain ⊆ 所有 hard_bounds 的交集`。若交集为空 → `feasibility.status=conflict`，写入 `violations` 并进入风险处置。
4. 建议值必须落在 `domain` 内；越界是阻断错误，必须改建议值或明确降级（降低目标/换材料/转人工），不得悄悄放宽约束。
5. 互斥/耦合约束（如“温度上限受材料耐温约束”）写成 `constraints` 项，附 locator + quote。
6. 缺来源的参数标 `status=unverified`，进入 `uncertainty.unverified_values`，不能当成已验证事实。

## 2. 响应指标口径与预测（→ response_model.json）

1. 每个指标写清：`name`、`unit`、`direction`（maximize/minimize/target）、`measurement_method`、`target`、`acceptable_min`（或 `acceptable_max`）。
2. 预测必须注明方法：`经验规则` / `历史拟合` / `代理模型`，并给 `confidence`（low/medium/high）与 `uncertainty`（如 ±5 %）。
3. 参数→指标关系写入 `relations`：方向（positive/negative/nonmonotonic）+ note + basis（locator + quote）。
4. 没有历史数据就写 `经验规则` 并低置信，不得伪装成拟合结果。
5. 同一指标两个来源不一致：都记录，标 `source_conflict`，在 `limitations` 说明采用哪一个及理由。

## 3. 搜索策略（→ search_model.json）

1. 方法四选一：`grid`（网格）、`sequential`（序贯/分阶段筛选）、`bayes`（贝叶斯优化）、`staged`（多阶段）。
2. 必须写明：`initial_points`（初始点怎么选）、`per_round`（每轮补哪些点）、`stop_criteria`（收敛与停止判据）。
3. 候选点由 `scripts/main.py doe --seed 0` 确定性生成：推荐中心点优先，再按“到中心归一化距离 → 参数序 → 取值”字典序排列；固定 seed 复现同一组点。
4. 停止判据示例：连续两轮最优响应改善 < 阈值；预算耗尽；可行域已充分覆盖；约束冲突无可行解。
5. 每轮评估结果与取舍理由写进 `candidates[].rationale` 与 `change_log`，保证可复核。

## 4. 成本/时间/多目标取舍（→ tradeoff_model.json）

1. 把机时、耗材、样品数、通量计入目标，`cost_items` 逐项给单位、数量、单价、小计与来源。
2. `total` 与 `time_hours` 必须与 `cost_items` 自洽（可加总）。
3. `tradeoff_note` / `acceptance_criteria` 必须回答：成本受限时牺牲多少响应指标可接受、多目标怎么取舍。
4. 无法核实单价/机时 → 标 `locator=""`、`quote=""`，写入 `pending_human`，不编报价。

## 5. 风险与异常处置（→ plan.risks / exception_handling）

必须覆盖至少五类，并给“降级 / 转人工 / 终止”判断：

| 情形 | 处置 |
|---|---|
| 无可行解（可行域为空） | `exception_handling` 记录，`status` 降级，转人工重定目标/换材料 |
| 约束冲突（目标温度 vs 材料耐温） | 记录 `violations`，选择：降低目标 / 换材料 / 转人工，绝不悄悄改约束 |
| 数据缺失 | 标 `uncertainty.missing_data`，给出可执行的下限，`pending_human` 待补 |
| 设备报错 | 记录，给出重试/换设备/终止判据 |
| 指标互相矛盾 | 双来源都记录，标 `source_conflict`，说明取舍理由 |

## 6. 确认项（→ plan.confirmations / pending_human）

安全、伦理、设备可用性、专家确认四类都要列。未确认内容写入 `pending_human`，不得把未确认建议写成可执行事实。

## 7. 提交前自检清单（逐条勾选，对应评分维度）

- [ ] 已实际读取 `/input/case.json` + `task.md` + 材料内容（非仅文件名/标签）
- [ ] 关键参数含名称/单位/建议值/可行区间/步进，全部落在设备量程/材料耐温/安全阈值内
- [ ] 每条参数有约束来源与逐字 quote，冲突已标 `source_conflict`
- [ ] 响应指标含口径/单位/测量方法/方向/目标/可接受下限，预测方式与不确定度已披露
- [ ] 搜索策略写明初始点、每轮补点、收敛与停止判据；候选点可由 `scripts/main.py doe` 复现
- [ ] 实验成本与时间预算有明细口径，多目标取舍说明“牺牲多少可接受”
- [ ] 覆盖假设、变量、对照、步骤、参数、资源、判读标准
- [ ] 风险与异常处置覆盖：无可行解、约束冲突、数据缺失、设备报错、指标矛盾
- [ ] 确认项（安全/伦理/设备可用性/专家确认）已列，未确认内容标注 `pending_human`
- [ ] 五层中间表示均已回填且通过 `scripts/validate_plan.py`（exit 0 或仅警告）
- [ ] `result.json`：`case_id` 正确、`status` 如实、`sources/uncertainty/change_log` 齐全、`limitations` 如实
- [ ] 可运行 `python scripts/selftest.py`，退出码 0

## 8. 边界

一个 Skill 只解决“有界参数优化”一类任务；不做通用大而全。无真实材料时输出 `insufficient_evidence` 并列出缺口，不用示例填充。交付前在非作者环境（不同机器/Python 版本）跑 `selftest.py`。
