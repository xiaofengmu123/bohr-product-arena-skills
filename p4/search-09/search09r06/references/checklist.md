# 自检清单（对应评分维度，交付前逐条勾选）

每项失败都要在 `result.json` 的 `errors`/`degradations`/`limitations` 中如实记录，不得删改掩盖。

## A. 实际效果（40 分）

- [ ] 产出"维度—判断—证据—等级"完整评价表，而非摘要换说法。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确（RCT/观察性/综述/诊断/临床前/质性/调查）。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 抽查通过，未命中已处理）。
- [ ] 覆盖研究设计、证据充分性、偏倚风险、报告完整性四类维度，并含该文献类型特有检查点。
- [ ] 第二人按 `locator + quote` 能独立复核并复现同一判断（重跑 `index → verify → grade → assemble` 得同一等级）。

## B. 使用增益（25 分）

- [ ] 维度覆盖优于泛化摘要（量表驱动，而非自由发挥）。
- [ ] 单篇评价所需人工复核条目数下降（弃权规则避免逐条补缺；`evidence_id` 直接定位锚点）。
- [ ] 结论可由同量表、双人复核对照验证（字段结构统一、判定值受限、`grade.derivation` 可复算）。

## C. 创新增益（15 分）

- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] **中间表示与可复核**已落实：`plan.json`/`evidence_index.json`/`assessment_matrix.json` 三个中间产物落盘，`result.json` 由 `assemble` 确定性重放装配。
- [ ] 量表映射、证据优先顺序（`basis`）、分级阈值、弃权规则有实质改进且文档化（见 `references/rubric.md`）。
- [ ] 说明相对通用方案摘要、公开报告清单、期刊编辑初筛的边界与价值。

## D. 可靠体验（10 分）

- [ ] 能被正确触发与调用：`python3 -B scripts/main.py skeleton` 可跑通。
- [ ] `python3 -B scripts/selftest.py` 退出码 0（空目录/坏 JSON/缺输入 + round-trip 全通过）。
- [ ] 不同研究类型、不同篇幅、重复运行下持续产出可用评价表（确定性、无随机数）。
- [ ] 已识别并处置：全文不可得 / 仅摘要 / 图表不可解析 / 语言或预印本特殊 / 关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（见 `references/rubric.md`）。
- [ ] 量表、提示、脚本、证据定位模板可被他人直接复用。
- [ ] 沉淀了可分享的评价案例、失败经验与真实应用价值说明（写入 `change_log`/`limitations`）。

## F. 运行产物完整性

- [ ] 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架。
- [ ] `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、`status`、`sources`、`uncertainty`、`change_log`。
- [ ] 主产物格式合法（JSON 可解析；`assessment.md` 可读；若有 Office/图件含真实 PDF/PNG 预览）。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] 最后跑过 `scripts/main.py validate` 且无 error 级问题。
