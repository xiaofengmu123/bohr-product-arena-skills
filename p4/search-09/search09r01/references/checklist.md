# 自检清单（对应评分维度，逐条勾选）

交付前逐条勾选。失败项必须写进 `result.json` 的 `errors`/`degradations`/`limitations`，
不得删改掩盖。脚本侧可跑 `python3 -B scripts/selftest.py` 自动核对 F 类硬项。

## A. 实际效果（40 分）

- [ ] 产出“维度—判断—证据—等级”完整评价表，而非摘要改写。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 `quote` 逐字存在于所标 `locator`；`evidence_map` 含 `quote_sha256` 且 `verify` 抽查通过。
- [ ] 覆盖研究设计、证据充分性、偏倚风险、报告完整性四类维度 + 文献类型特有检查点。
- [ ] 第二人按 `locator + quote + evidence_map` 能独立复核并给出可比结论。

## B. 使用增益（25 分）

- [ ] 维度覆盖优于泛化摘要（量表驱动，非自由发挥）。
- [ ] 单篇人工复核条目数下降（弃权规则避免逐条补缺）。
- [ ] 结论可由同量表、双人复核对照验证（字段结构统一、verdict 受限）。

## C. 创新增益（15 分）

- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 量表映射、证据优先顺序、分级阈值、弃权规则有实质改进且文档化（见 rubric.md）。
- [ ] 说明相对通用摘要/公开报告清单/期刊编辑初筛的边界与价值。
- [ ] 本版证据溯源强化：每条结论带 locator+逐字 quote+SHA256+char_span+match 状态，
      `evidence_map` 比单一 `evidence_records` 更细，可机器核验。

## D. 可靠体验（10 分）

- [ ] 能被正确触发与调用：`python3 -B scripts/main.py skeleton` 可跑通。
- [ ] 不同研究类型、不同篇幅、重复运行下持续产出可用评价表（确定性、无随机数）。
- [ ] 已识别并处置：全文不可得 / 仅摘要 / 图表不可解析 / 预印本或语言特殊 / 关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（见 rubric.md）。
- [ ] 量表、脚本、证据定位模板可被他人直接复用。
- [ ] 沉淀了可分享的评价案例、失败经验与真实应用价值说明。

## F. 运行产物完整性（硬项，selftest.py 自动核对）

- [ ] 前三分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架。
- [ ] `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、
      `status`、`sources`、`uncertainty`、`change_log`、`evidence_map`。
- [ ] 主产物格式合法（JSON 可解析；assessment.md 存在）。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] `scripts/main.py validate` 无 error 级问题；`selftest.py` 与 `main.py self-test` 通过
      （空目录/坏 JSON/缺输入均退出码 0）。
