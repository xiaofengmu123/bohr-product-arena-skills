# 七类约束检查项库与判定口径（check library）

本文件是 `scripts/core_impl.py` 判定逻辑的书面规格；脚本实现与本文件不一致时以本文件为准。每项判定必须给出来源类型：**SOP / 设备手册 / 伦理规范 / 价格或预约合同 / 方案原文**。

## 判定枚举

`verdict`：`ok | conditional | blocked | unconfirmed | missing | conflict`

- `ok`：方案写明且证据支持，无未解决条件。
- `conditional`：有条件可行。必须列出逐条可执行 `conditions`。
- `blocked`：硬阻断（设备不具备、参数越界、审批过期、货期赶不上、样本量不可满足）。必须写清缺什么。
- `unconfirmed`：信息不足无法判定。必须写"需谁、确认什么、约多久"，`needs_expert=true`。
- `missing`：方案本身未写。**不得默认满足**。给出检索范围（scope）。
- `conflict`：同一指标两处来源不一致，或单位不可比。两条都保留。

`resource_state`（仅 `equipment_materials`）：`已具备 available | 可外协 outsource | 需采购 purchase | 不具备 unavailable | 未知 unknown`。

证据规则：`ok/conditional/blocked/conflict` 必须带 `locator + quote`；`missing/unconfirmed` 可只给 scope，但不得伪造引文。

## 1. assumptions_variables（假设与变量）

| 检查项 | 判定 | 依据来源 |
|---|---|---|
| 是否有可检验研究问题/假设 | 有→ok；无→missing | 方案原文 |
| 是否列出关键假设（样本稳定性、批次一致性、设备精度） | 有→ok；无→conditional（补假设） | 方案原文/SOP |
| 每个变量是否有单位或分组水平 | 有→ok；无→conditional（补单位/水平与测量方法） | 方案原文/设备手册 |
| 变量与判读标准是否指向同一目标 | 不一致→conflict | 方案原文 |

## 2. controls（对照组）

| 检查项 | 判定 | 依据来源 |
|---|---|---|
| 是否写明对照/参照条件 | 有→ok；无→missing | 方案原文 |
| 每臂样本量与分组数 | 齐→ok；缺臂数→conditional；均缺→missing | 方案原文/样本清单 |
| 涉及干预/活体实验是否有随机化与盲法 | 缺→conditional | SOP/伦理规范 |

## 3. equipment_materials（设备与材料）

| 检查项 | 判定 | 依据来源 |
|---|---|---|
| 所需设备是否在清单登记 | 未登记→missing | 方案原文/资源清单 |
| 设备状态（已具备/外协/需采购/不具备） | 不具备→blocked；采购/外协→conditional（货期+价格）；状态未写→unconfirmed | 资源清单/合同 |
| 机时是否冲突 | `利用率 = need / (capacity - booked)`；>1→conditional；capacity 未给→unconfirmed | 设备手册/预约合同 |
| 材料库存 vs 用量 | `缺量 = max(0, 用量 - 库存)`；`包数 = ceil(缺量/包装规格)`；不足→conditional（采购） | 库存清单/合同 |
| 采购货期 vs 计划开始 | `可用天数 = 计划开始 - 决策日`；`货期 > 可用天数`→blocked | 合同/排期 |

单位统一：时间→min，体积→µL，质量→mg，货币→CNY；跨单位不可比时标 `conflict`，不得强行折算。

## 4. time_cost（时间成本）

| 检查项 | 算术 | 判定 |
|---|---|---|
| 顺序时长 | `wall = Σ step.duration_min` | 有缺失步骤→conditional |
| 关键路径 | DAG 最长路径；有依赖环则回退顺序和并注明 | 供排期判定 |
| 时间窗 | `wall` vs 题目时间窗 | 超窗→blocked |
| 人力 | `人工周均 = operator_hours / weeks`，`weeks=max(span/7,1)` | 超在册人力→conditional |
| 采购前置 | 最长货期 vs 可用天数 | 来不及→blocked |
| 预算 | `已标价成本 = Σ 设备小时×单价 + Σ 材料缺量×单价` | 超支→conditional；无预算但存在标价成本→conditional；全缺→missing |

成本仅统计已标价项，未标价项在 notes 中声明"未计入"。

## 5. parameter_ranges（参数范围）

| 检查项 | 判定 | 依据来源 |
|---|---|---|
| 是否给出关键参数 | 无→missing | 方案原文 |
| 参数域是否给定 | 无→unconfirmed | 设备手册/SOP |
| 取值是否在域内 | 在→ok；越界→blocked；单位不可比→conflict | 方案原文/参数域 |
| 离散允许集 | 取值 ∈ allowed→ok；∉→blocked | SOP/手册 |

## 6. risks_exceptions（风险与异常）

| 检查项 | 判定 | 触发条件 |
|---|---|---|
| 已声明风险是否有处置/降级/转人工 | 无处置→conditional；全有→ok；无风险清单→missing | 方案原文 |
| 采购无货期 | conditional | 需采购/外协且无 lead_time |
| 瓶颈设备 | conditional | 机时利用率 > 80% |
| 样本余量 | conditional（可选扩展） | 需求 > 库存×0.8 |

## 7. ethics_safety（伦理与安全）

| 检查项 | 判定 | 依据来源 |
|---|---|---|
| 人类/动物/危化品/个人信息信号 | 关键词扫描，无信号→ok（注明"文本扫描非法规裁定"） | 方案原文 |
| 对应审批记录 | 已批准且未过期→ok；待批→unconfirmed；过期/驳回→blocked；无记录→unconfirmed | 伦理批件/审批表单 |
| 危化品/生物安全处置 SOP | 缺→conditional | SOP/安全规范 |

伦理/安全未确认一律 `needs_expert=true`，不得写成可执行事实。

## 版本比较与推荐口径

- 每个版本按 `penalty = Σ(ok=0, conditional=1, unconfirmed/missing=1.5, conflict=2, blocked=4)`。
- 排序：`penalty → 关键路径 → 成本`。
- 相邻版本 diff：步骤增删、参数变化、问题消解/新引入。
- 推荐版本为 penalty 最低者；若仍含 blocked，总体结论保持 blocked，推荐仅为相对最优。
