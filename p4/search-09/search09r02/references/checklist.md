# 自检清单（交付前逐条勾选）

每条失败都要如实写入 `result.json` 的 `errors`/`degradations`/`limitations`，不得删改掩盖。编号对应官方评分维度。

## A. 实际效果（40 分）

- [ ] A1 产出"维度—判断—证据—等级"完整评价表，非摘要改写；`assessment[]` 每条含 `dimension/checkpoint/verdict/judgment/locator/quote/basis`。
- [ ] A2 每条判断与原文一致；研究设计类型识别正确（§4 特有检查点匹配）。
- [ ] A3 每条非弃权 `quote` 逐字存在于所标 `locator`——已跑 `scripts/litqa.py verify` 且 `fail=0`。
- [ ] A4 四类维度（研究设计/证据充分性/偏倚风险/报告完整性）全覆盖 + 文献类型特有检查点。
- [ ] A5 第二个人按 `locator + quote` 能独立复核并得到可比结论（判定值受限、量表统一）。

## B. 使用增益（25 分）

- [ ] B1 维度覆盖优于泛化摘要：量表驱动（rubric.md），逐检查点判定。
- [ ] B2 人工复核条目数下降：弃权规则避免逐条补缺；只复核 `critical` 与非弃权项。
- [ ] B3 同量表、双人复核可对照：字段结构统一、`verdict` 取值受限、分级阈值显式。

## C. 创新增益（15 分）

- [ ] C1 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] C2 量表映射、证据优先顺序（§5）、分级阈值（§6）、弃权规则（§2）有实质改进且文档化。
- [ ] C3 已说明相对通用摘要/报告清单/编辑初筛的边界与价值（rubric.md §8）。

## D. 可靠体验（10 分）

- [ ] D1 可被正确触发：`python3 -B scripts/main.py skeleton` 可跑通并立即落盘。
- [ ] D2 不同研究类型/篇幅/重复运行持续可用：确定性（无随机数）、固定定位与阈值。
- [ ] D3 已识别处置：全文不可得/仅摘要/图表不可解析/预印本或语言特殊/关键证据缺失 → `insufficient` 或 `degradations`/`limitations` 如实标注。

## E. 生态贡献（10 分）

- [ ] E1 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（rubric.md）。
- [ ] E2 量表、提示、脚本、证据定位模板可被他人直接复用（`scripts/` 仅标准库）。
- [ ] E3 沉淀可分享的评价案例、失败经验与真实价值说明。

## F. 运行产物完整性

- [ ] F1 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架（`runtime.skeleton_epoch` 存在）。
- [ ] F2 `result.json` 含 `case_id`、`output_contract` 全部字段（原样回填，缺值 `null`/`[]`）、`status`、`sources`、`uncertainty`、`change_log`。
- [ ] F3 主产物 JSON 可解析；`assessment.md` 可读。
- [ ] F4 `status` 诚实：`ok/partial/insufficient_evidence/input_error`，不编造数据与结论。
- [ ] F5 最后跑过 `scripts/main.py validate` 且无 error 级问题；`scripts/selftest.py` 六场景全过。

## 硬性红线（违反即不及格）

- 不得编造 DOI、引用、数值、全文、引文或运行状态。
- 不得用"看起来合理"的判断替代原文证据；证据不足必须弃权。
- 不得照抄题面或预制答案；必须读取实际 `/input` 文件后产出。
