# 评分维度对齐表（rubric alignment）

本表把题目五个评分维度逐条映射到流程阶段、确定性检查与自检项，使"流程设计—证据产出—评分点"三者可互相追溯。`scripts/checks.py` 内置同一张 `RUBRIC` 表，`selfcheck` 会校验五个维度均有映射。

| 评分维度（分值） | 评分点 | 流程阶段 | 确定性检查 | 自检项 |
|---|---|---|---|---|
| 实际效果（40） | 完成度15 / 正确性15 / 完整性5 / 可核验5 | 阶段2 逐句编辑、阶段5 定稿 | `sentence_coverage`、`number_unit_conservation`、`citation_conservation`、`figure_table_pointer`、`hedge_conservation`、`evidence_traceability` | `result.json` 可解析；`deliverable` 存在；每个 unit 含原文/改文/类型/理由四要素；`errors==0` |
| 使用增益（25） | 质量15 / 效率5 / 可验证5 | 阶段3 双通道审校、阶段4 修复 | `gain_metrics`、`errors==0` | `gain_metrics` 由 `edits.json`+`check_report.json` 公开推导：`total_units / edited_units / edit_types / hedge_notes_written / errors_caught / evidence_entries`，可与基础 Agent 在同输入下对照 |
| 创新增益（15） | 新增能力5 / 方法改进5 / 创新证据5 | 阶段3 双通道审校（先编辑、后独立回读复核） | `hedge_conservation`、`terminology_consistency`、`abbreviation_first_use` | `change_log` 记录双通道；限定强度守恒与术语回扫可独立复现，无需隐藏白板 |
| 可靠体验（10） | 触发3 / 稳定4 / 异常3 | 阶段0 脚手架、阶段6 自检 | `deterministic_scaffold`、`selfcheck_ok` | `selftest.py` 覆盖空目录、坏 JSON、缺输入、坏编码、超长五场景，全部退出码 0 且产出合法 `result.json` |
| 生态贡献（10） | 文档3 / 复用4 / 分享3 | 全程（`SKILL.md`、`references/`、`scripts/` 随包交付） | `references_present`、`stdlib_only` | `references/*.md` 存在；脚本仅标准库；术语表/风格规则/评分表可直接复用于其他稿件 |

## 使用方式

1. Agent 每完成一个流程阶段，即对照本表确认该阶段产出的证据落在对应评分维度上。
2. 阶段6 `selfcheck` 通过 = 五个维度均有产出物与自检项闭环；任一维度缺映射即返回非零退出码。
3. `gain_metrics` 是"使用增益可验证性"的唯一合法证据来源，必须由脚本从公开产物计算，不允许手工杜撰评测分数。

> 说明：本表不构造任何评测分数；分数与白板由平台提供。本表只保证"每个评分维度都有一条可复现的证据链"。
