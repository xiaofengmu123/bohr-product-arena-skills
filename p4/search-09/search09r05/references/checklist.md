# 自检清单（逐条可勾选，对应评分维度）

交付前逐条勾选；每项失败都如实写入 `result.json` 的 `errors`/`degradations`/`limitations`，不得删改掩盖。

## A. 实际效果（40 分）

- [ ] 产出"维度—判断—证据—等级"完整评价表，而非摘要换说法。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 的 `fail==0`）。
- [ ] 覆盖研究设计/证据充分性/偏倚风险/报告完整性四类维度，并含该文献类型特有检查点（见 `rubric.md` §2.5）。
- [ ] 第二个人按 `locator + quote` 能独立复核并得出可比结论（判定词受控、字段统一）。

## B. 使用增益（25 分）

- [ ] 维度覆盖由量表驱动（`rubric.md`），优于泛化摘要。
- [ ] 单篇评价人工复核条目数下降（弃权规则避免逐条补缺）。
- [ ] 结论可由同量表、双人复核对照验证（判定词受限、阈值确定）。

## C. 创新增益（15 分）

- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 契约优先 + `contract_alignment`：逐字段回填 `output_contract` 且可核验。
- [ ] `change_log` 全程审计：每次写盘一条结构化事件，可逐条回放。
- [ ] 说明相对通用方案/公开报告清单/编辑初筛的边界与价值。

## D. 可靠体验（10 分）

- [ ] `python3 -B scripts/main.py skeleton` 可跑通并先落盘骨架。
- [ ] `python3 -B scripts/main.py validate` 无 error 级问题。
- [ ] `python3 -B scripts/selftest.py` 退出码 0。
- [ ] 不同研究类型/篇幅/重复运行下持续产出可用评价表（确定性、无随机数）。
- [ ] 已识别处置：全文不可得/仅摘要/图表不可解析/预印本或语言特殊/关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（`rubric.md`）。
- [ ] 量表、脚本、证据定位模板可被他人直接复用（均标准库、相对路径）。
- [ ] 沉淀可分享的评价案例与失败经验。

## F. 运行产物完整性

- [ ] 前 3 分钟内已用 `main.py skeleton` 落盘 `result.json` 骨架。
- [ ] `result.json` 含 `case_id`、`output_contract` 全部字段（缺值 null/[]）、`status`、`sources`、`uncertainty`、`change_log`、`runtime`。
- [ ] `contract_alignment.field_count` 与题目契约字段数一致。
- [ ] `change_log` 非空，含 `skeleton_written` 起点与 `grade_computed`/`validated` 等增量事件。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] JSON 可解析；`assessment.md` 存在且含证据锚点；Office/图件产物附 PDF/PNG 预览。
