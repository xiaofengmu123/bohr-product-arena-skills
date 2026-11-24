# 评分维度对齐方法（rubric alignment）

本文件说明如何把题面评分维度逐条落到流程步骤与自检项，供 Agent 在执行时按图索骥，也供复核人检查"每一步是否命中评分点"。

## 一、六类要点（题面要求覆盖的核心内容）

题面明确结果完整性考察六类要点：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。它们与 `result.json`/工作产物的对应关系如下：

| 六类要点 | 落盘位置 | 完成判据 |
|---|---|---|
| 问题拆解 | `/app/work/decomposition.md` | ≥2 个 `SQ` 子问题 + 依赖边（依赖/→/前置） |
| 检索覆盖 | `/app/work/search_plan.md` | 含"顺序""停止""来源"，检索式去重 |
| 证据回链 | `result.json.evidence_matrix` + `conclusions.evidence_ids` | 每条结论证据 id 可命中，每行有 locator+quote |
| 冲突证据 | `result.json.uncertainty.conflicts` | 每条并列两个来源 + 解决说明 |
| 综合结论 | `result.json.research_product` | 结论非空且与问题、证据范围一致 |
| 研究空白 | `result.json.uncertainty.gaps` / `pending_human` | 显式写"缺什么"，非套话 |

`scripts/main.py selfcheck` 会计算这六项并写入 `result.json.rubric_coverage.six_key_points`，同时给出 `backlink_rate`、`locator_quote_rate` 等可核验比率。

## 二、五维评分 → 流程 → 自检

| 评分维度（分值） | 评分点 | 流程落点 | 自检项 | 证明物 |
|---|---|---|---|---|
| 实际效果（40） | 任务完成度 15 | P1 拆解 + P2 取证 | 六类要点中的拆解/检索覆盖 | decomposition.md + search_plan.md |
| | 结果正确性 15 | P3 综合 | 结论↔证据一一对应，冲突如实呈现 | result.json 结论 + conflicts |
| | 结果完整性 5 | P3 综合 | 六类要点全覆盖 | rubric_coverage.six_key_points 全真 |
| | 结果可核验性 5 | P2 取证 | 每行 evidence 有 locator+quote | evidence_matrix + evidence_map.json |
| 使用增益（25） | 质量增益 15 | P0 骨架 + P3 综合 | 骨架先落盘、结论可回链 | change_log + 回链率 |
| | 效率增益 5 | P2 检索规划 | 检索去重、停止条件明确 | search_plan.md + trace.jsonl |
| | 增益可验证 5 | 全流程留痕 | trace.jsonl 记录参数与返回 | `/app/work/trace.jsonl` |
| 创新增益（15） | 新增能力 5 | 证据卡片机 | conflicts 双来源并列、不静默择一 | uncertainty.conflicts |
| | 方法改进 5 | 拆解+检索去重 | 依赖边 + 停止条件 | decomposition.md |
| | 创新证据 5 | 自检量化 | rubric_coverage 量化六点/五维 | result.json.rubric_coverage |
| 可靠体验（10） | 调用易用 3 | 文档 | 子命令清晰、相对路径可执行 | SKILL.md §5 |
| | 运行稳定 4 | 确定性脚本 | selfcheck 通过、重复运行一致 | scripts/main.py |
| | 异常处理 3 | 降级策略 | 空输入/坏 JSON 退出码 0 | errors/degradations |
| 生态贡献（10） | 文档完整 3 | SKILL.md + references | 用途/依赖/权限/示例/限制齐全 | 本包文档 |
| | 资源复用 4 | 模板沉淀 | decomposition/search_plan/evidence 模板可复用 | references/evidence_template.md |
| | 分享价值 3 | 自检脚本 | selftest.py 可独立跑通 | scripts/selftest.py |
| GitHub 加分（+5） | — | 提交时 | 根目录 `GITHUB_URL.txt` 第一行为链接 | GITHUB_URL.txt |

## 三、对齐纪律

1. **只对"材料内是否有支持"下结论**，不越界判定文献真伪；`strength` 三级（established/inferred/conjectural）对应证据强度。
2. **冲突不择一**：同一指标两来源不一致时，两条都记录并标 `source_conflict`/`undetermined`。
3. **效率以留痕为准**：每步工具调用记录输入参数、返回摘要、失败重试，使"减少无效检索"可被复核，而不是靠自述。
4. **增益不靠文字自述**：质量增益由回链率/覆盖率的 `rubric_coverage.metrics` 证明；效率增益由 `trace.jsonl` 的轮次与去重证明。
