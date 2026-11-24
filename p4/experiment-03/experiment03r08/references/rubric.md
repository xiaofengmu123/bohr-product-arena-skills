# 评分维度映射与门槛（rubric）

本文件把题目 5 个评分维度、16 个评分点写成可机检的门槛。`scripts/validate_plan.py`
内置同版本 RUBRIC 表，对 `/app/output/result.json` 逐条计算 `rubric_audit` 并写
`audit_report.json`。权重只用于标注来源，不用于虚构得分。

| 编号 | 维度 | 分值 | 门槛（gate） |
|-|-|-|-|
| R01 | 实际效果·任务完成度 | 15 | parameters、response_metrics 非空；search_strategy.method 非空；cost_budget 有 items 或 total |
| R02 | 实际效果·结果正确性 | 15 | 每个 parameter 的 suggested_value ∈ feasible_range，且不越 equipment/material/safety 边界 |
| R03 | 实际效果·结果完整性 | 5 | 同时覆盖关键参数、响应指标、搜索策略、实验成本，并含 assumptions/variables/controls/procedures |
| R04 | 实际效果·结果可核验性 | 5 | search_strategy.seed 为数值；initial_points/per_round/stop_criteria 非空；evidence_map.claims 非空 |
| R05 | 使用增益·质量增益 | 15 | 可行域约束全部有 provenance（locator+quote）；constraint_count≥1 且无越界 |
| R06 | 使用增益·效率增益 | 5 | search_strategy 给出每轮补点与停止判据（避免人工改参/返工的显式判据） |
| R07 | 使用增益·增益可验证性 | 5 | 若有 baseline（baseline_score/baseline_notes）则记录；否则在 notes 标注“缺基线，不做自评” |
| R08 | 创新增益·新增能力 | 5 | 存在有界参数域（feasible_range 有 min/max）且 constraint_count≥1（约束感知搜索） |
| R09 | 创新增益·方法改进 | 5 | response_metrics 含 prediction_method；search_strategy.method ∈ {grid,sequential,bayes,staged} |
| R10 | 创新增益·创新证据 | 5 | 至少 1 条 response_metric 的 prediction_note + search_strategy.stop_criteria 的收敛判据同时存在 |
| R11 | 可靠体验·调用与易用性 | 3 | search_strategy.method、initial_points 非空；procedures 非空 |
| R12 | 可靠体验·运行稳定性 | 4 | seed 数值 + 确定性 doe 可复现；errors/degradations 如实记录 |
| R13 | 可靠体验·异常处理 | 3 | exception_handling 覆盖五类场景（无可行解/约束冲突/数据缺失/设备报错/指标矛盾） |
| R14 | 生态贡献·文档完整性 | 3 | confirmations 覆盖安全/伦理/设备可用性/专家确认；SKILL.md 与 references 存在 |
| R15 | 生态贡献·资源可复用性 | 4 | scripts/*.py 存在且只依赖标准库；references/output_contract.md 与 rubric.md 存在 |
| R16 | 生态贡献·分享推广价值 | 3 | result.json 与 artifacts（plan.md/plan.json）存在；change_log 与 limitations 如实 |

## 状态语义

- `ok`：R01–R16 中所有硬门槛（R01、R02、R04、R13、R16）通过，其余至多 warning。
- `partial`：已有骨架/部分内容，硬门槛未全过；`validate_plan.py` 返回 exit 2（警告）。
- `insufficient_evidence`：材料不足以确定参数/响应/约束，未编造数值与来源。
- `input_error`：输入目录不可读或 case.json 损坏且无法恢复 case_id。

`status` 与 `rubric_audit` 必须一致：`status=ok` 但硬门槛未过，validate 返回 exit 1。
