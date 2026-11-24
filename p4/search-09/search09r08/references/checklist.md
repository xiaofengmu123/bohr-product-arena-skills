# 自检清单（逐条可勾选，对应评分维度）

交付前逐条勾选。每项失败都要在 `result.json` 的 `errors`/`degradations`/`limitations` 中如实记录，不得删改掩盖。自检项编号与 `references/rubric.md` §1 映射表一致。

## A. 实际效果（40 分）

- [ ] A1 产出"维度—判断—证据—等级"完整评价表，而非摘要换说法。
- [ ] A2 `assessment` 至少覆盖研究设计、证据充分性、偏倚风险、报告完整性四类维度。
- [ ] A3 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] A4 每条 `critical=true` 的判断带 `locator` + 逐字 `quote`。
- [ ] A5 `scripts/litqa.py verify` 抽查通过（无 MISSING）。
- [ ] A6 含该文献类型特有检查点（RCT/队列/系统综述等，见 rubric.md §3）。
- [ ] A7 第二人按 `locator + quote` 能独立复核并得出可比结论（判定值受限词汇表）。

## B. 使用增益（25 分）

- [ ] B1 维度覆盖与判断准确度优于泛化摘要（量表驱动，非自由发挥）。
- [ ] B2 单篇评价所需人工复核条目数下降（弃权规则避免逐条补缺）。
- [ ] B3 结论可由同量表、双人复核对照验证（字段结构统一、判定值受限）。

## C. 创新增益（15 分）

- [ ] C1 具备量表驱动、证据锚定、分级可解释、缺证据可弃权四项结构化能力。
- [ ] C2 量表映射、证据优先顺序、分级阈值、弃权规则有实质改进且已文档化。
- [ ] C3 说明相对通用方案摘要/公开报告清单/期刊编辑初筛的边界与价值。

## D. 可靠体验（10 分）

- [ ] D1 `python3 -B scripts/main.py skeleton` 一次跑通，退出码 0。
- [ ] D2 无随机数、固定定位与阈值，重复运行同一输入结果一致。
- [ ] D3 已识别并处置：全文不可得 / 仅摘要 / 图表不可解析 / 语言或预印本特殊 / 关键证据缺失。

## E. 生态贡献（10 分）

- [ ] E1 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（见 rubric.md）。
- [ ] E2 量表、提示、脚本、证据定位模板（`src#pN`）可被他人直接复用。
- [ ] E3 沉淀可分享评价案例、失败经验与真实应用价值说明。

## F. 运行产物完整性

- [ ] F1 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架。
- [ ] F2 `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、`status`、`sources`、`uncertainty`、`pending_human`、`change_log`、`runtime`。
- [ ] F3 主产物格式合法：`result.json` 可解析；`assessment.md` 存在且含评价表 + 证据锚点。
- [ ] F4 `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] F5 最后跑过 `scripts/main.py validate` 且报告无 ERROR 级问题。
- [ ] F6 `scripts/selftest.py` 通过（frontmatter / 体积 / 四场景自检 / --help）。
