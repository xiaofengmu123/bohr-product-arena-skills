# 自检清单：评分维度 → 检查项 → 常见失败与修复

对应 SKILL.md 第 6 节的逐条清单。每个检查项都映射到题目评分维度，并给出 validate 报告里对应字段与修复动作。`validate` 的退出码 0=可用（含警告）、1=必须修复；修复后增量更新 `change_log`，不要整段重写。

## 1. 任务完成度（实际效果·15）

| 检查项 | 常见失败 | validate 位置 | 修复 |
|-|-|-|-|
| 假设可检验、可证伪 | 复述目标，无方向/无预期效应 | 人工检查 hypothesis | 写成"处理 X → 指标 Y 沿某方向变化"并给出量级 |
| 变量有操作定义/单位/量程 | 变量只有名字 | coverage 为空 | 补 independent/dependent 的操作定义与单位 |
| 步骤绑定设备/试剂/时长 | 步骤写"准备—处理—检测" | time_cost.steps 缺 duration_hours | 每步补时长与设备/试剂 |

## 2. 正确性（实际效果·15）

| 检查项 | 常见失败 | validate 位置 | 修复 |
|-|-|-|-|
| 变量与测量方式匹配 | 因变量单位与测量方法不符 | 人工核对 | 对齐单位与方法 |
| 每个对照排除某类混杂 | 只有空白组 | controls.groups 单一 | 补阳性/阴性/梯度并写 confounder_addressed |
| 参数在设备量程内 | 参数区间超量程 | arithmetic_conflicts | 收窄区间或换设备，写 basis |
| 引用与规格真实 | 编型号/价格/DOI | unfounded_claims | 补 basis+locator 或改 not_reported |

## 3. 完整性（实际效果·5）

六要素 6 项均非空，且 `sources`/`uncertainty`/`pending_human`/`limitations` 完整。缺一项即 `status` 不得为 `ok`；validate 的 `coverage` 逐项列出。修复：对每项空缺要么补内容，要么把 status 降为 `partial` 并把缺口写入 `limitations`。

## 4. 可核验性（实际效果·5）

| 检查项 | 常见失败 | 修复 |
|-|-|-|
| result.json 有版本与来源定位 | 无 sources/change_log | 补 sources、change_log |
| 金标准可复算 | total_hours 与步骤和不一致 | 对齐算术，写 computed |
| plan.md 与 result.json 一致 | 两边数值漂移 | 生成后同步两处 |

## 5. 使用增益（25）

- 质量增益：候选方案比较必须有量程/预算/排期算术支撑，不得写"方案 A 更优"而无数值。
- 效率增益：把参数裁剪写成一页可复用的表（parameter_ranges 模板），减少返工。
- 增益可验证性：每一步裁剪写 `computed` 依据，评审可复算。

## 6. 创新增益（15）

- 新增能力：按研究目标反推对照（由假设的混杂因素推出所需对照类型）。
- 方法改进：按量程与预算裁剪参数（设备量程与预算双约束取交集）。
- 创新证据：风险预案与替代路径至少落实一类，且替代路径写清触发条件与代价。

## 7. 易用性（可靠体验·3）

按 SKILL.md 第 2/3/4 节能完成一次生成并判断可用：`probe → scaffold → 分析填写 → validate`。判断可用的最小标准是 validate 退出码 0 且 `status` 诚实。

## 8. 稳定性（可靠体验·4）

| 场景 | 期望 | selftest |
|-|-|-|
| 空输入 | `insufficient_evidence`，退出码 0 | check_empty_input |
| 坏 JSON/编码 | `input_error`，记录后继续 | check_bad_json |
| 重复运行 | 字节一致 | check_determinism |

## 9. 异常处理（可靠体验·3）

| 异常 | 处置 | 落点 |
|-|-|-|
| 输入缺失（无设备参数/预算） | 写 not_reported | limitations + pending_human |
| 约束冲突（时间窗 vs 样本量） | 写冲突双方 + 两种替代 | uncertainty(kind=conflict) + schedule_conflicts |
| 依赖不可用（缺货/排期满） | 写替代路径 | equipment.items.alternative + risks.alternative_path |
| 生成内容不确定 | 写 severity 与 resolution | uncertainty |

## 10. 生态贡献（10）

- 文档完整：SKILL.md 写清用途/输入输出/异常/示例；references 两份。
- 可复用：plan_schema_and_evidence.md 的六要素模板、参数表模板、basis 词汇可直接拷贝。
- 分享价值：`case_given` 与真实报价的区分、locator+quote 溯源、不确定性分类是可迁移的做法。

## 常见修复速查

- validate 报 `unfounded_claims` → 该数值/型号/价格无来源：补 `basis`；若材料确实没有，写 `not_reported` 并把缺口列入 `pending_human`。
- validate 报 `coverage` 某要素为空 → 补齐该要素；若缺材料，写 `not_reported` 并降 status 为 `partial`。
- validate 报 `arithmetic_conflicts` → 核对步骤时长之和、total_hours、参数区间与设备量程；修正数值或把冲突写入 `uncertainty`。
- validate 报证据缺 locator/quote → 回到 PDF/合同原文，逐字复制原句作为 quote。
