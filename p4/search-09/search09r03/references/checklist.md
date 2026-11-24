# 自检清单（逐条可勾选，对应评分维度）

交付前逐条勾选。任一失败都要写入 `result.json` 的 `errors`/`degradations`/`limitations`，不得删改掩盖；机器自检可用 `python3 -B scripts/selftest.py`（或 `python3 -B scripts/main.py self-test`）一键执行 S1–S7。

## A. 实际效果（40 分）

- [ ] 产出"维度—判断—证据—等级"完整评价表（`assessment` + `grades`），而非摘要改写。
- [ ] 每条判断与原文一致；研究设计类型与统计口径识别正确。
- [ ] 每条 `quote` 逐字存在于所标 `locator`（`scripts/litqa.py verify` 全部 `found=true`）。
- [ ] 覆盖研究设计/证据充分性/偏倚风险/报告完整性四类维度，并含文献类型特有检查点。
- [ ] 第二人按 `locator + quote` 能独立复核并得出可比结论（判定值取自受限词表）。

## B. 使用增益（25 分）

- [ ] 维度覆盖优于泛化摘要（量表驱动，四类维度 + 特有检查点）。
- [ ] 单篇人工复核条目数下降（`insufficient_evidence` 弃权，不逐条补缺）。
- [ ] 同量表双人复核可比（字段结构统一、判定值受限、分级阈值固定）。

## C. 创新增益（15 分）

- [ ] 具备基础 Agent 没有的结构化能力：量表驱动、证据锚定、分级可解释、缺证据可弃权。
- [ ] 量表映射、证据优先顺序、分级阈值、弃权规则有实质改进且文档化（`references/rubric.md`）。
- [ ] 说明相对通用摘要/公开报告清单/编辑初筛的边界与价值（SKILL.md §1/§6）。

## D. 可靠体验（10 分）

- [ ] 能被正确触发与调用：`python3 -B scripts/main.py skeleton` 可跑通。
- [ ] 不同研究类型、不同篇幅、重复运行下持续产出可用评价表（确定性：无随机数、无墙钟）。
- [ ] 已识别并处置：全文不可得/仅摘要/图表不可解析/语言或预印本特殊/关键证据缺失。

## E. 生态贡献（10 分）

- [ ] 量表定义、维度含义、分级阈值、适用文献类型、依赖与限制齐全（`references/rubric.md`）。
- [ ] 量表、提示、脚本、证据定位模板可被他人直接复用（标准库、相对路径）。
- [ ] 沉淀可分享的评价案例、失败经验与真实应用价值说明（`SKILL.md` 降级策略）。

## F. 运行产物完整性

- [ ] 前 3 分钟内已用 `scripts/main.py skeleton` 落盘 `result.json` 骨架（P0 最先完成）。
- [ ] `result.json` 含 `case_id`、题目 `output_contract` 全部字段（原样回填，缺值 null/[]）、`status`、`sources`、`uncertainty`、`change_log`。
- [ ] 主产物格式合法（JSON 可解析；`assessment.md` 可读）。
- [ ] `status` 诚实：`ok | partial | insufficient_evidence | input_error`，不编造数据与结论。
- [ ] 最后跑过 `scripts/main.py validate` 且无 error 级问题。

## G. 确定性专项（本变体）

- [ ] 同一输入两次运行 `result.json` 逐字节一致（`selftest.py` S5）。
- [ ] 空目录/坏 JSON/缺输入三种降级均退出码 0 且 JSON 合法（S1–S3）。
- [ ] `run_id` 由输入内容哈希派生，`change_log` 幂等 upsert，重复运行不漂移。
