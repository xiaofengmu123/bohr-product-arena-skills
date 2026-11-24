# 七类约束审查细则（feasibility_rubric）

本文件给 Agent 提供可逐条勾选的判定标准。判定必须落到方案的具体章节、参数与清单行；"方案没写"一律记为 `missing`，不得默认满足。每条 `ok/conditional/blocked/conflict` 必须带 `locator + 逐字 quote`。

## 1. 判定枚举

| verdict | 含义 | 必须给出 |
|---|---|---|
| `ok` | 材料已证明该约束满足 | `evidence`（locator + 逐字 quote） |
| `conditional` | 满足某条件后可行 | `evidence` + `conditions`（逐条可执行） |
| `blocked` | 材料证明当前不可行/缺关键资源 | `evidence` + 缺什么 |
| `unconfirmed` | 无法确认（需审批/专家/外部答复） | `needs_expert=true` + 需谁、确认什么、约多久 |
| `missing` | 方案/材料未写该信息 | 无需 evidence，但要写明"缺哪项" |
| `conflict` | 两处来源不一致 | 两条 `evidence` 都记录，notes 标 `source_conflict` |

`resource_state` 仅用于 `equipment_materials` 维度：`已具备 | 可外协 | 需采购 | 不具备 | 未知`。
`confidence` 取 `high | medium | low`，须与证据强度一致：单一自述为 `medium` 以下，经清单/SOP 交叉核对方可 `high`。

## 2. 七类约束与检查要点

### 2.1 假设与变量（assumptions_variables）

- 研究目标是否转成**可检验问题**（能否给出"是/否"判据）。
- 自变量、因变量、协变量是否命名，是否给出单位与测量方法。
- 假设是否**可证伪**：若结果相反，方案是否给出判别。
- 是否存在隐含假设未写明（如"设备精度足够""样本同质"），未写明记为 `missing`。

### 2.2 对照组（controls）

- 是否有阳性对照、阴性/空白对照、历史对照或配对设计。
- 对照是否**排除混杂**（时间、批次、操作者、环境）。
- 样本分配是否说明随机化/配对方式；未说明记 `missing`。
- 多版方案比较时，对照设置差异要单列。

### 2.3 设备材料（equipment_materials）

- 设备型号、精度、量程、附件是否与现有设备清单逐项比对。
- 耗材规格、批次、保存条件是否满足。
- 判定 `resource_state`：已具备 / 可外协 / 需采购 / 不具备 / 未知。
- 核对货期与到货时间是否赶得上实验窗口；合同/报价要引用原文与日期。

### 2.4 时间成本（time_cost）

- 按步骤拆出：设备占用、人工操作、等待周期、检测周转。
- 用 `sum_time` 累加并写算式；区分串行与可并行步骤（并行需在 notes 说明）。
- 核对总时长与排期、样本可用窗口、人员投入是否匹配。
- 输出 `total_minutes`、`total_hours`、`formula`、`unparseable`。

### 2.5 参数范围（parameter_ranges）

- 每个关键参数是否有依据（文献 / SOP / 预实验数据）。
- 用 `check_range` / `check_parameter_against_domain` 校验取值是否落域。
- 说明容差与超界处置；无依据的参数记 `missing`，不得默认合理。

### 2.6 风险与异常（risks_exceptions）

- 识别：设备故障、材料批次差异、样本量不足、参数越界、排期冲突。
- 每条给出降级路径、替代方案、转人工节点。
- 风险等级与触发条件要可判定，不写"注意风险"这类空话。

### 2.7 伦理与安全（ethics_safety）

- 是否涉及动物、人体样本、危险试剂、个人数据。
- 对应哪一级审批、需哪些材料、大致周期。
- 未经审批不得写成可执行事实；无法确认记 `unconfirmed` + `needs_expert=true`。

## 3. 算术规则（必须算出来，不用感觉）

| 目的 | 函数 | 关键输出 |
|---|---|---|
| 时长累加 | `parse_duration` + `sum_time` | `total_minutes`、`total_hours`、`formula`、`unparseable` |
| 区间校验 | `check_range(value, {min,max,inclusive})` | `in_range`、`formula` |
| 参数域 | `check_parameter_against_domain(value, domain)` | `allowed`、`domain_size` |
| 成本累加 | `sum_cost(items)` | 按币种 `totals`、`formula` |
| 样本量 | `check_sample(needed, available)` | `sufficient`、`shortfall`、`formula` |

**算式示例（示意，非正式案例）**

- 时长：`准备(30min) + 处理(120min) = 150 min`，`total_hours = 2.5`。
- 区间：`18 <= 22 <= 25` → `in_range = true`（spec 为 `{min:18,max:25}`）。
- 样本量：`available(12) >= needed(10)` → `sufficient = true`，`shortfall = 0`。

## 4. 证据定位格式

- `case.json#plan.<i>.steps[<j>]`
- `case.json#contracts.<key>`
- `materials/<name>.md#<标题或行>`
- `sources/<name>.pdf#p<页>`
- `task.md#<行>`

`evidence.quote` 必须是逐字原文；过长时截取决定性子句并给定位，不转写、不补全。同一指标两处来源冲突时两条都记，标 `source_conflict`。

## 5. 方案比较（plan_comparison）

多版候选方案时，对每个维度输出一列比较，最后给出 `推荐方案 + 理由 + 相对风险`。比较必须引用同一口径的量化指标（时间/成本/样本量/参数域），不得只写"更优"。建议结构：

```json
{"dimension": "time_cost", "v1": "...", "v2": "...", "preferred": "v2", "basis": "..."}
```

## 6. 交付前自查（对应评分维度）

- [ ] 七类各 ≥1 条证据化判断，无"未写当满足"。
- [ ] 每条 `ok/conditional/blocked/conflict` 均有 `locator + quote`。
- [ ] 数值结论有算式（时间/区间/样本/成本）。
- [ ] 指出方案自身缺失信息与矛盾处。
- [ ] 结论可执行：`conditional` 有 `conditions`，`blocked` 写缺什么，`unconfirmed` 写需谁确认。

## 7. 常见失败模式与修正

| 失败模式 | 表现 | 修正 |
|---|---|---|
| 未写当满足 | 方案没提样本量，却判 `ok` | 改为 `missing`，在 notes 写明"缺样本量依据" |
| 时间靠感觉 | 写"约 2 周"但无步骤分解 | 用 `sum_time` 拆步累加并给 `formula` |
| 结论悬空 | 只写"建议进一步确认" | 改 `unconfirmed` 并写需谁、确认什么、约多久 |
| 证据转写 | quote 与原文措辞不一致 | 回到原文截取决定性子句，逐字复制 |
| 冲突被抹平 | 两处价格不一致只取其一 | 两条都记，标 `source_conflict` |
| 资源状态含混 | 设备"大概有" | 落到清单行，判 `已具备/可外协/需采购/不具备/未知` |
| 参数无依据 | 温度写 22℃ 但不给来源 | 记 `missing` 或补 SOP/文献定位 |
| 伦理默认通过 | 涉及人体样本却判 `ok` | 改 `unconfirmed` + `needs_expert=true` |

## 8. 输出模板（结构示意，非正式答案）

```json
{
  "id": "tc-2", "dimension": "time_cost", "claim": "总时长约 5.5 小时，可行",
  "verdict": "conditional",
  "conditions": ["设备占用需提前 3 天预约", "检测周转按 1 天计"],
  "evidence": [{"locator": "case.json#plan.0.steps[1]", "quote": "逐字原文", "kind": "plan"}],
  "formula": "准备(30min) + 处理(120min) + 检测(180min) = 330 min",
  "confidence": "medium", "needs_expert": false, "notes": []
}
```

模板只规定字段与关系，具体 claim、conditions、quote 必须来自实际材料；不得把模板文字当作结论。

## 9. 参数依据来源分级

参数是否"有据"决定 `verdict` 与 `confidence`。按证据强度从高到低：

| 级别 | 来源 | 判定 | confidence |
|---|---|---|---|
| A | 设备手册 / 现行 SOP / 标准方法 | 可判 `ok` | high |
| B | 同条件预实验数据 / 内部历史记录 | 可判 `ok`，注明批次 | medium–high |
| C | 同行评议文献（给出 DOI/页码） | 可判 `ok`，注明适用条件 | medium |
| D | 方案自述、无外部来源 | `missing` 或 `conditional` | low |
| E | 无任何说明 | `missing` | low |

同一参数有多个来源且数值冲突时，取保守值并记 `conflict`；不得取"看起来合理"的一个。

## 10. 方案比较与推荐写法

多版方案比较时，建议每个维度一行，量化列可空但不可编造：

| 维度 | v1 | v2 | v3 | 推荐 | 依据 |
|---|---|---|---|---|---|
| 时间 | 330 min | 210 min | 480 min | v2 | 步骤累加，见 formula |
| 成本 | 需采购 2 项 | 已具备 | 需外协 1 项 | v2 | 合同原文 |
| 样本量 | 12/10 足够 | 8/10 不足 | 15/10 足够 | v1 或 v3 | check_sample |
| 主要风险 | 设备排队 | 参数越界 | 伦理待批 | 视优先级 | 见 risks_exceptions |

推荐结论必须写"推荐 + 理由 + 相对风险"，不能只写"v2 更优"。若关键维度信息缺失，推荐改为 `conditional` 并列出补齐条件。
