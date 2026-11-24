# 评分维度 → 流程与自检映射表

本文件把题目五个评分维度逐条映射到 Skill 的流程动作、脚本产物与自检项，供 Agent 在执行中随时对照，也供评审复核"文字自述 ↔ 实际运行证据"是否一致。

## 1. 实际效果（40 分）

| 评分点（分值） | 流程动作 | 脚本/产物 | 自检项 |
|---|---|---|---|
| 任务完成度 15 | Step 1–2：从种子沿后向/前向扩展，产出文献集与证据 | `record --kind paper/edge` → `results.papers/edges` | papers/edges/clusters/paths 非空且可追溯来路 |
| 结果正确性 15 | Step 2：核实文献真实性、边方向、版本合并、关联依据 | `finalize` 的 `dedupe_papers` → `results.id_remap`/`merge_evidence`；边方向统一 `cites` | 文献真实存在；方向无猜测；同文多版本合并正确 |
| 结果完整性 5 | Step 2–3：覆盖关键被引出处、后续改进、平行工作 | `results.pruned`、`limitations` | 覆盖关键节点；遗漏显式写入 limitations |
| 结果可核验性 5 | Step 1–3：每条边/结论带 locator+quote | `record --kind source/edge` → `sources`/`evidence` | seed 标识、level、边、数据源可独立复核 |

## 2. 使用增益（25 分）

| 评分点（分值） | 增益来源 | 证据 |
|---|---|---|
| 质量增益 15 | 方向规范化、跨源去重、证据链三层保证 | `finalize` 输出的 `id_remap`、每条边的 `evidence` |
| 效率增益 5 | 去重/排序/路径由脚本确定性完成，减少人工核验与翻页 | `rank`、`path`、`dedupe` 子命令 |
| 增益可验证性 5 | 自检脚本在 demo 上跑通完整链路 | `scripts/selftest.py` 的通过/失败计数 |

## 3. 创新增益（15 分）

| 评分点（分值） | 创新点 | 落地方式 |
|---|---|---|
| 新增能力 5 | 跨库身份对齐、方向感知扩展、可解释路径 | 并查集多键合并；`path --reverse` 前向追踪；`path` 输出逐跳 `citing/cited` 与 evidence |
| 方法改进 5 | 去重键优先级、停止判据、排序"距离优先" | `dedupe_papers` 强/中/弱键分层；`rank_papers` 距离权重最大 |
| 创新证据 5 | 输出自述方法边界 | `rank`/`path` 输出 `note`；去重对 title-only 只记候选不误合并 |

## 4. 可靠体验（10 分）

| 评分点（分值） | 要求 | 自检项 |
|---|---|---|
| 调用与易用性 3 | 三步完成追踪 | `bootstrap → record → finalize` 说明与示例一致 |
| 运行稳定性 4 | 跨学科/覆盖度/重复运行稳定 | 脚本纯标准库、确定性排序与布局；`selftest` 空输入/坏 JSON 通过 |
| 异常处理 3 | 识别并处置五类异常 | 空输入→`insufficient_evidence`；坏 JSON→`degradations`；字段冲突→`uncertainty`；撤稿/限流→`pending_human`/`errors` |

## 5. 生态贡献（10 分）

| 评分点（分值） | 要求 | 产物 |
|---|---|---|
| 文档完整性 3 | 用途/调用/输入输出/异常/维护齐全 | SKILL.md + 本映射表 + evidence-contract.md |
| 资源可复用性 4 | 脚本/边表/去重规则可复用 | `scripts/*.py`、`papers.csv`/`edges.csv` 稳定字段 |
| 分享价值 3 | 沉淀可分享的追踪模板 | 边表结构、去重键优先级、路径图模板 |

## 执行中对照口诀

- 每次 `record` 都带 `--reason`，确保 `change_log` 可解释；
- 每条 `edge` 必须有 `evidence`，缺失即标 `not_reported`；
- 每次身份合并都写 `members`/`keys`，合并证据可复核；
- 每步落盘前先确认 `result.json` 是合法 JSON（可用 `finalize` 校验）。