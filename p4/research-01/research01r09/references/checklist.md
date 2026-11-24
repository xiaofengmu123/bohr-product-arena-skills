# 自检清单与降级规则（references）

本文件是 SKILL.md 第 6、7 节的展开，供运行时逐条核对；`scripts/selftest.py` 会把其中可机检的部分自动跑一遍。

## 六点覆盖自检（对应评分维度）

- [ ] **问题拆解**：`decomposition.md` 含 ≥2 个可独立检索的子问题（SQ1…SQn），并标注依赖边（`->` 或“依赖”）。
- [ ] **检索计划**：`search_plan.md` 写清顺序、来源范围、停止条件；不重复查同一批文献。
- [ ] **证据回链**：每条结论的 `evidence_ids` 都能命中 `evidence_matrix`；每行有 `locator + quote`，且 quote 逐字取自材料。
- [ ] **冲突证据**：`uncertainty.conflicts` 逐条并列至少两个来源与解决说明，不静默择一。
- [ ] **综合结论**：`research_product.conclusions` 非空，且与研究问题和证据范围一致。
- [ ] **研究空白**：`uncertainty.gaps` / `pending_human` 显式写出“缺什么”，不用“尚需进一步研究”一类套话。
- [ ] **完整合法**：`selfcheck` 通过；`result.json` 可被 `json.load` 解析；题目 `output_contract` 字段全部原样回填。
- [ ] **异常处理**：空输入、坏 JSON、坏编码、PDF 解析失败均记入 `errors`/`degradations`，退出码 0。

## 降级规则

| 场景 | 处置 |
|---|---|
| 输入为空 / 缺 case.json | `status=insufficient_evidence`，`pending_human` 列缺口，不编造 |
| case.json 坏 JSON | `status=input_error`，`limitations` 说明，其余材料继续处理 |
| 文件 >2MB | 截断读取，记入 `degradations` |
| PDF 无文本层 / 扫描件 | `judgment=extraction_failed`，建议 OCR，不硬读像素 |
| 证据冲突无法裁定 | 并列记录 + 标 `undetermined`，不强行取一方 |
| 缺软件 / 缺权限 | 记入 `limitations`/`errors`，降级为最小合法产物 |
| 重复运行 | 固定证据 id、确定性布局，同一输入两次结果一致 |

## result.json 字段速查

- `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- `status`：`ok | partial | insufficient_evidence | input_error`，必须诚实。
- `research_product`：`{type, question, summary, conclusions[]}`；conclusion 带 `id/text/evidence_ids/strength`。
- `evidence_matrix[]`：每行 `id/subquestion/source/locator/quote/claim/judgment/reason/conclusion_ids`。
- `uncertainty`：`{conflicts, biases, gaps, undetermined}`。
- `sources/change_log/limitations/errors/degradations/pending_human`：数组；`runtime_seconds`：数值。
