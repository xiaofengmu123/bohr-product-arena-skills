# 可行性门控审查判定口径（rubric）

本文件定义七类约束的判定口径、状态枚举、证据等级与算术规则；是 `SKILL.md §6` 的细则，
也是 `scripts/selftest.py` 校验口径的书面依据。所有阈值均可被复核、可被覆盖。

## 1. 七类约束与判定依据来源

| 维度 | 判定问题 | 依据来源（按优先级） | 材料缺失时 |
|---|---|---|---|
| assumptions_variables | 研究目标是否已转成可检验问题、自变量/因变量/混杂是否列全、假设边界是否写明 | 方案原文、文献、SOP | `missing` |
| controls | 是否设阳性/阴性/空白/内标/质控，且能解释为何足以排除混杂 | 方案原文、方法学文献 | `missing` |
| equipment_materials | 设备型号/精度/附件、耗材规格是否与现有条件或合同比对，状态是否明确 | 设备手册、采购/预约合同、库存清单 | `missing` 或 `unconfirmed` |
| time_cost | 设备占用、人工、等待、检测周转是否拆分并累加，预算是否与价格合同一致 | 步骤时长、价格/预约合同、排期表 | `missing` |
| parameter_ranges | 每个关键参数是否有名义值、容差与依据，是否落在允许域内 | 设备手册、SOP、文献 | `missing` |
| risks_exceptions | 设备故障、批次差异、样本不足、参数越界是否有降级路径与转人工节点 | 方案原文、SOP、历史记录 | `missing` |
| ethics_safety | 是否涉及动物/人体样本/危险试剂/个人数据，对应审批级别、材料与时长 | 伦理规范、机构 SOP、审批表单 | `unconfirmed` + `needs_expert=true` |

## 2. verdict 枚举

| verdict | 含义 | 必须提供 |
|---|---|---|
| `ok` | 有逐字证据支撑，判定为已满足 | `evidence` |
| `conditional` | 有条件可行，条件须逐条可执行 | `evidence` + `conditions[]` |
| `blocked` | 存在硬阻断（缺口/超预算/越界），写清缺什么 | `evidence` |
| `missing` | 方案未写该项信息，不得默认满足 | 定位该章节 |
| `unconfirmed` | 未经审批/无法确认，明确“需谁、确认什么、约多久” | `action` + `needs_expert=true` |
| `conflict` | 两来源不一致，两者都记录 | 两条 `evidence` + `notes=source_conflict` |

## 3. severity 与 evidence_grade

- `severity`：`blocker`（阻断开工）、`major`（显著影响结论）、`minor`、`info`。
- `evidence_grade`：
  - `E1`：材料逐字原文（quote 为原文子串，selftest C09 可核验）；
  - `E2`：由原文数值经脚本派生的算术结论（必须带 `arithmetic.expr+result+unit`）；
  - `E3`：基于证据的推断判断（结论强于“建议确认”，但需在 claim 标明是推断）。

## 4. 资源状态枚举（仅 equipment_materials 维度）

`已具备 | 可外协 | 需采购 | 不具备 | 未知`。判“已具备”必须有合同/库存/设备清单的逐字证据；判“需采购”须带价格与货期（若有）；无法判断写 `未知` 并进入 `unconfirmed`。

## 5. 算术规则（与 checks.py 对应）

- **T1 时间**：`total_h = Σ step.duration`，按 key 后缀/字符串推断单位（day=24h、week=168h、min=1/60h、h=1）；推断出的单位必须标 `assumed_unit`，不冒充精确。求和默认“单份/单批”，若方案要求按 n 份缩放，须在 `review.time_cost` 另写缩放算式。
- **B1 预算**：`采购总额 = Σ price×qty`（缺 qty 记 1）；与方案预算上限比较，`over = total − cap`。无带价格合同项时记为 `not_computable`，不得猜价。
- **P1 参数域**：`value ∈ [min,max]`（含端点）或 `value ∈ allowed[]`；方案未给参数值 → `unknown`。
- **S1 样本**：`shortfall = max(0, required − available)`；同时核对保存条件与伦理审批进度的匹配。
- **C1 机时**：`required machine_h ≤ Σ capacity_h`；缺 `capacity_h/available_hours/slots` → `unknown`。
- **W1 工期**：`total_h ≤ window_h`；窗口由 `window/deadline/timeline` 类字段发现并转小时。

## 6. 门控聚合（结构性，供审查员确认）

对每个方案：任一 `blocked` 发现 → 方案门控 `blocked`；否则任一 `conditional` → `conditional`；
否则存在 `unknown` → `unknown`；否则 `ok`。排序键 `(门控状态, 阻断数, 条件数, 总工时, plan_id)`
确定性比较，同输入同顺序。

## 7. 伦理与安全审批路径（一般性指南，以本机构规定为准）

| 涉及对象 | 常见审批 | 常见材料 | 经验时长 |
|---|---|---|---|
| 人体样本/临床数据 | 伦理委员会（IRB/EC） | 方案、知情同意、样本来源说明 | 数周—数月 |
| 动物实验 | 实验动物伦理/福利审查 | 动物使用方案、人员资质 | 数周 |
| 危险试剂/生物安全 | 实验室安全与生物安全备案 | MSDS、风险评估表 | 数天—数周 |
| 个人数据 | 数据合规审查 | 数据处理协议、去标识方案 | 数天—数周 |

上述时长仅为一般量级，不替代机构审批事实；未经批件一律 `unconfirmed`。

## 8. 冲突与“未写当满足”

- 同一指标两来源不一致：两条都记，`verdict="conflict"`，`notes=["source_conflict"]`。
- 判定为 `ok` 的 claim 中禁止出现“默认满足/默认已具备/视为满足/假定已具备”等措辞；selftest C11 会拦截。
- 任一维度没有任何材料提及 → 至少一条 `missing`，保证七类维度不空转。
