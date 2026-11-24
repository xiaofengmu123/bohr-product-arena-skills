# 交付前自检清单（对应评分维度）

本清单是 `SKILL.md` 中自检项的展开版本，交付/每轮 finalize 前逐条核对。括号内标注对应评分维度。

## A. 实际效果（40 分）

- [ ] **问题拆解**：`decomposition.md` 含 ≥2 个可独立检索的子问题（SQ1…SQn），每个子问题标注依赖边（前置/可并行），并写出每个子问题需要的证据类型（数值/机制/方法/时间范围/比较关系）。
- [ ] **检索覆盖**：`search_plan.md` 写清检索顺序、来源范围、停止条件；`search_log.jsonl` 记录每次检索的 query、来源范围、命中数、停止原因；不存在「同一 query 重复检索」。
- [ ] **证据回链**：每条结论 `evidence_ids` 命中 `evidence_matrix`；每行证据有 `locator` + 逐字 `quote`，且 `quote` 逐字取自材料（`selfcheck` 的 `quote_verify` 通过）。
- [ ] **冲突证据**：`uncertainty.conflicts` 逐条并列 ≥2 个来源（带 locator+quote）与解决说明，未静默择一。
- [ ] **综合结论**：`research_product.conclusions` 非空，与研究问题和证据范围一致，每条有 `strength`。
- [ ] **研究空白**：`uncertainty.gaps` 逐条带 `kind` + `search_evidence`，`pending_human` 显式写「缺什么」，不用套话。

## B. 使用增益（25 分）

- [ ] **质量增益可验证**：`decomposition.md`、`search_plan.md`、`evidence_cards.jsonl`、`search_log.jsonl` 与 `result.json` 一一对应，可独立复跑；中间产物由脚本确定性组装，不靠手工改 JSON。
- [ ] **效率增益可验证**：检索无重复（`search_log` 去重）、`evidence_cards.jsonl` 增量追加、结论只引用已检索材料，减少重写工作量。
- [ ] **对照可复现**：同一输入两次运行，证据 id 与布局一致（`scripts/selftest.py` 的 determinism 测试通过）。

## C. 创新增益（15 分）

- [ ] **新增能力**：gap 的三分类（`retrieval_empty`/`not_searched`/`material_missing`）与 `search_log` 回链，使「研究空白」可被逐条复核（基础 Agent 难以稳定实现）。
- [ ] **方法改进**：`claim_type`（fact/inference/gap/conflict）× `strength` 双层标注，加上 `verification`（verbatim/paraphrase/unverified），让断言强度与动词选择自洽。
- [ ] **创新边界**：`references/uncertainty_guide.md` 写明该设计解决什么、不解决什么（不做单篇精读、不做文献真伪审查）。

## D. 可靠体验（10 分）

- [ ] **调用易用**：`python3 -B scripts/main.py --help` 退出码 0；`discover/skeleton/merge/finalize/selfcheck` 均可独立执行。
- [ ] **运行稳定**：`scripts/selftest.py` 全部通过（空输入、坏 JSON、坏编码、契约回填、quote 守卫、确定性）。
- [ ] **异常处理**：空输入、坏 JSON、坏编码、PDF 解析失败、检索失败均写入 `errors`/`degradations`/`limitations`，进程退出码 0，绝不崩溃。

## E. 生态贡献（10 分）

- [ ] **文档完整**：`SKILL.md` 写清用途、适用条件、输入/输出契约、执行流程、预算、自检、降级与边界。
- [ ] **资源可复用**：`decomposition.md`、`search_plan.md`、`evidence_cards.jsonl` 的字段结构可直接复用于其他综述/筛选/趋势任务。
- [ ] **分享价值**：脚本只依赖标准库，`references/` 讲清规则，无需作者个人文件即可运行。

## 交付前最后三条硬性检查

1. `python3 -B scripts/main.py selfcheck` 输出的 `ok == true`，且 `critical_failures` 为空。
2. `result.json` 可被 `json.load` 解析，且含 `case_id`、`status`、`research_product`、`evidence_matrix`、`uncertainty`、`sources`、`change_log`，以及题目 `output_contract` 的全部字段（缺值给 `null`/`[]`）。
3. 没有任何数字、DOI、文献、运行结果是在材料之外编造的；凡材料没有的，均落在 `gaps`/`pending_human`/`limitations`/`not_reported` 之一。
