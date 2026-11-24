# 可行性审查判定口径（Feasibility Rubric）

本文件是 SKILL.md 第 6 节的展开：七类约束的判定标准、状态枚举与算术规则。审查时逐条对照，判定依据必须来自材料原文（SOP、设备手册、伦理规范、合同、方案步骤），不来自记忆。

## 1. 七类约束与核心判定问题

| 维度 | 键 | 核心判定问题 |
|---|---|---|
| 假设与变量 | `assumptions_variables` | 研究目标是否转为可检验问题？自变量/因变量/混杂变量是否定义、可测量？假设是否可被数据支持或反驳？ |
| 对照组 | `controls` | 对照是否真正隔离目标变量？阳性/阴性/空白/自身对照是否合理？随机化/盲法/重复是否说明？ |
| 设备材料 | `equipment_materials` | 型号、精度、附件、耗材规格是否与现有条件清单比对？区分已具备/可外协/需采购/不具备。 |
| 时间成本 | `time_cost` | 每步设备占用、人工操作、等待周期、检测周转是否可累加？总时长/总成本是否有算式依据？ |
| 参数范围 | `parameter_ranges` | 关键参数域是否有限？上下界/容差是否有文献或手册依据？方案取值是否落在范围内？ |
| 风险与异常 | `risks_exceptions` | 设备故障、材料批次差异、样本量不足、参数越界是否有降级路径、替代方案、转人工节点？ |
| 伦理与安全 | `ethics_safety` | 是否涉及动物/人体样本/危险试剂/个人数据？对应哪级审批、哪些材料、约需多久？ |

## 2. 审查项 verdict 枚举

- `ok`：方案该维度有明确说明，且与材料/设备能力/时间/样本约束一致，无未满足项。
- `conditional`：有条件可行。必须写清 `conditions`（逐条可执行：谁、做什么、何时完成）。
- `blocked`：明确缺某资源/审批/样本，写清缺什么与最小解除路径。
- `unconfirmed`：伦理/安全/设备可用性等未经审批或无法确认，`needs_expert=true`，明确"需谁、确认什么、约多久"。
- `missing`：方案未写该维度内容。**"未写"不等于"满足"**，不得默认通过。
- `conflict`：方案内部或方案与合同/清单/规范互相矛盾，两条来源都记录并标 `source_conflict`。

## 3. resource_state（仅 equipment_materials）

- `已具备`：现有清单可查、型号/精度/附件匹配。
- `可外协`：有可替代外协渠道且已核实可承接。
- `需采购`：缺货或货期不满足，需下单并写清采购前置确认项。
- `不具备`：无可用渠道或规格不达标，`blocked`。
- `未知`：无法从材料确认，`unconfirmed` + `needs_expert=true`。

## 4. 算术规则（用 review.py 确定性计算）

- **时间**：`sum_time(steps)`。steps 每项含 `label` 与 `hours`；缺数值的步骤记 `skip_reason="missing"`，不计入总和但须在 `notes` 标注未计入。输出 `formula`（如 `1.5 + 2.25 = 3.75`）。
- **参数范围**：`check_range(value, low, high)`。边界按 `inclusive_low/high` 判定；值或边界缺失 → `missing`；越界 → `conflict`。
- **样本量**：`check_sample_size(required, available)`。`ratio = available/required`；`ratio >= 1.0` → `ok`，否则 `blocked`。
- **成本**：`sum_cost(items)`。items 每项含 `label` 与 `cost`；输出 `formula` 与 `total`。
- 所有数值结论必须在 `evidence` 或 `notes` 给出**算式与依据**，禁止"感觉一个数"。

## 5. 整体结论 verdict

- `可行`：七类维度全部 `ok`（无 missing/blocked/conflict/unconfirmed）。
- `有条件可行`：存在 `conditional`，且全部条件已列清、可执行。
- `不可行`：存在 `blocked` 且无替代路径。
- `无法判定`：材料不足或关键维度 `unconfirmed/missing`。

## 6. 证据定位规范

- `case.json#plan.<i>.steps[<j>]`：候选方案第 i 版第 j 步。
- `case.json#contracts.<key>`：资源/价格/预约合同条目。
- `materials/<name>.md#<标题或行>`、`sources/<name>.pdf#p<页>`、`task.md#<行>`。
- `evidence.quote` 必须是逐字原文；过长时截取决定性子句并给定位，不转写、不补全。

## 7. 多版候选方案比较协议

本任务固定案例通常带 3 个版本候选方案（`plan.0/1/2`）。比较须同一口径、可复跑：

1. **逐版生成审查项**：对每个版本独立跑七类审查，`evidence.locator` 使用 `case.json#plan.<i>.…` 区分版本。
2. **确定性排序**：`compare_plans(plans)`，每版分数 = Σ verdict 权重（`ok=0, conditional=-1, missing=-1, unconfirmed=-1, conflict=-2, blocked=-3`）；同分按 `plan_id` 字典序，保证复跑一致。
3. **输出比较表**：`plan_comparison` 每项含 `plan_id / rank / score / counts / blocked / conditional / missing`，并把排序依据写进 `notes`（引 `compare_plans` 算式）。
4. **不得主观打分**：排序只由阻断项、条件项、缺失项与算术可行性决定；文字评价只解释"为什么该版更可开工"。

## 8. contract-fidelity 审计协议（本版重点）

`result.json` 顶层必须同时满足"题目契约字段"与"本 Skill 元信息/审查主产物"两类要求。审计要点：

1. `contract_fidelity.source` 说明契约来源：`case.json#output_contract`、`case.json#output_contract(list)` 或 `task_standard_fallback`。
2. `contract_fidelity.fields` 逐条记录字段名、推断类型、回填状态（`echoed` 原样回填 / `defaulted` 用契约默认值 / `schema_built` 由对象 schema 递归构造）。
3. `unknown_fields_preserved=true`，`renamed_fields=[]`，`dropped_fields=[]`：证明未改名、未删未知键。
4. 交付前用 `scripts/selftest.py` 复核：契约主区块存在、`case_id` 非空、`status` 合法、`change_log` 非空。

## 9. 脚本函数输出契约（供复用）

| 函数 | 输入 | 确定性输出 |
|---|---|---|
| `sum_time(steps)` | `[{label, hours}]` | `{total, unit, steps, formula}`，缺值步记 `skip_reason="missing"` 不计入总和 |
| `check_range(value, low, high)` | 数值 | `{value, low, high, verdict, reason}`，边界可设含/不含 |
| `check_sample_size(required, available)` | 数值 | `{required, available, ratio, verdict, reason}` |
| `sum_cost(items)` | `[{label, cost}]` | `{total, currency, items, formula}` |
| `compare_plans(plans)` | `[{plan_id, review_items}]` | 按 score 降序的 `[{plan_id, rank, score, counts, …}]` |
| `validate_review_item(item)` | 单条审查项 | 问题列表（空列表=合法） |
| `resolve_status(review)` | 七类审查 | `ok / partial / insufficient_evidence` |

所有函数纯函数、无随机数、无墙钟时间；相同输入复跑结果逐字节一致。`main.py` 幂等：已有 `result.json` 时只补齐缺失契约字段、保留已填内容，不覆盖审查结论。

