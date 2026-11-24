# 自检清单（对应评分维度）

交付前逐条勾选；每项失败都要在 `result.json` 的 `errors`/`degradations`/`limitations` 中如实记录，不得删改掩盖。

## A. 实际效果（40 分）

- [ ] 产出"维度—判断—证据—等级"完整评价表，不是摘要换说法。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/main.py verify` 抽查 mismatch=0）。
- [ ] 覆盖研究设计、证据充分性、偏倚风险、报告完整性四类维度，并含该文献类型特有检查点。
- [ ] 第二个人按 `locator + quote` 能独立复核并得出可比结论（判定值受限、量表一致）。

## B. 使用增益（25 分）

- [ ] 维度覆盖优于泛化摘要（量表驱动，逐检查点，而非自由发挥）。
- [ ] 单篇评价所需人工复核条目数下降（弃权规则避免逐条补缺，`merge` 支持分批增量）。
- [ ] 结论可由同量表、双人复核对照验证（字段结构统一、判定值受限、阈值一致）。

## C. 创新增益（15 分）

- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 架构差异化：先建证据索引（中间表示），再 verify（引文核验）→ grade（分级）→ validate（结构），"先证后评"。
- [ ] 量表映射、证据优先顺序、分级阈值、弃权规则有实质改进且文档化（见 `references/rubric.md`）。

## D. 可靠体验（10 分）

- [ ] 能被正确触发与调用：`python3 -B scripts/main.py skeleton` 可跑通，`self-test` 退出码 0。
- [ ] 不同研究类型、不同篇幅、重复运行下持续产出可用评价表（确定性、无随机数、固定排序）。
- [ ] 已识别并处置：全文不可得 / 仅摘要 / 图表不可解析 / 语言或预印本特殊 / 关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（见 `references/rubric.md`）。
- [ ] 量表、提示、脚本、证据定位模板可被他人直接复用（脚本只依赖标准库）。
- [ ] 沉淀了可分享的评价案例、失败经验与真实应用价值说明（`change_log`/`limitations` 保留过程）。

## F. 运行产物完整性

- [ ] 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架。
- [ ] `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、`status`、`sources`、`uncertainty`、`change_log`。
- [ ] 主产物格式合法（JSON 可解析；若产出 Office/图件同时给 PDF/PNG 预览）。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] 最后跑过 `scripts/main.py validate` 且无 error 级问题。

## 最小调用示例（示意，非正式案例）

```bash
python3 -B scripts/main.py skeleton --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/main.py index    --input ./demo/input --output ./demo/output --work ./demo/work
# Agent 依据量表评价，写入 ./demo/work/assessment_patch.json
python3 -B scripts/main.py merge    --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/main.py verify   --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/main.py grade    --input ./demo/input --output ./demo/output --work ./demo/work
python3 -B scripts/main.py validate --input ./demo/input --output ./demo/output --work ./demo/work
```

示意输出（标明为示例）：`result.json` 的 `status="partial"`、`grade.overall` 由阈值算出、`verification.summary.mismatch=0`；`assessment.md` 给出评价表与证据锚点。
