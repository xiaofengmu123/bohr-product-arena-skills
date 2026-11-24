# 自检清单（对应评分维度，交付前逐条勾选）

失败项必须写入 `result.json` 的 `errors`/`degradations`/`limitations`，不得删改掩盖。

## A. 实际效果（40 分）
- [ ] 产出“维度—判断—证据—等级”完整评价表，不是摘要改写。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 quote 逐字存在于所标 locator（`scripts/litqa.py verify` fail=0）。
- [ ] 覆盖 study_design/evidence/bias/reporting 四类维度 + 该文献类型特有检查点。
- [ ] 第二人按 locator+quote 能独立复核并得出可比结论。

## B. 使用增益（25 分）
- [ ] 维度覆盖优于泛化摘要（量表驱动，非自由发挥）。
- [ ] 单篇人工复核条目数下降（弃权规则避免逐条补缺）。
- [ ] 结论可由同量表双人复核对照（字段结构统一、判定值受限）。

## C. 创新增益（15 分）
- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 量表映射、证据优先顺序、分级阈值、弃权规则有实质改进且文档化（rubric.md）。
- [ ] 说明相对通用摘要/公开报告清单/编辑初筛的边界与价值。

## D. 可靠体验（10 分）
- [ ] `python3 -B scripts/main.py skeleton` 可跑通，3 分钟内落盘。
- [ ] 不同研究类型/篇幅/重复运行持续产出可用评价表（确定性、无随机数）。
- [ ] 已处置：全文不可得/仅摘要/图表不可解析/语言或预印本特殊/关键证据缺失。

## E. 生态贡献（10 分）
- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（rubric.md）。
- [ ] 量表、提示、脚本、证据定位模板可被他人直接复用。
- [ ] 沉淀可分享的评价案例、失败经验与真实应用价值。

## F. 运行产物完整性
- [ ] 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 result.json 骨架。
- [ ] result.json 含 case_id、output_contract 全部字段（原样回填）、status/sources/uncertainty/change_log。
- [ ] 主产物格式合法（JSON 可解析）。
- [ ] status 诚实（ok/partial/insufficient_evidence/input_error），不编造。
- [ ] 最后跑过 `scripts/main.py validate` 且无 error 级问题。
