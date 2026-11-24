# 自检清单（提交前逐条勾选）

本清单与评分维度一一对应，可由 `python -B scripts/main.py selfcheck` 自动执行机械项，
也可由人逐条勾选。勾选规则：只有证据真实、可复跑才打勾。

## A. 实际效果（40 分）

- [ ] A1 问题拆解：`review_question` 非空，`subquestions` ≥ 2 且写明依赖关系。
- [ ] A2 检索式可执行：每个 `query_blocks` 含 `database` / `fields` / `query` / `intent` / `date_limit` / `language_limit`，不退化成一个关键词串。
- [ ] A3 纳排标准条款化：每条有 `id` / `criterion` / `kind`（inclusion|exclusion）/ `priority`，且**先于筛选决定**落盘。
- [ ] A4 两轮筛选留痕：题录 + 全文两轮逐条 `decision`；每条 exclude 有 `reason` + `criterion_id`；计数可由 `prisma_flow` 重算。
- [ ] A5 质量评价可解释：逐维度 `rating` + `basis_locator` + `quote`，总体等级与“对结论的影响”都写明。
- [ ] A6 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`；每行证据有 `locator` + 逐字 `quote`。
- [ ] A7 冲突与空白：`synthesis.conflicts` 逐条并列两个来源；`synthesis.gaps` 落到具体缺口与证据状态。
- [ ] A8 结论不越界：结论只陈述证据范围内可支撑的内容，区分事实、推断、`not_reported`。

## B. 使用增益（25 分）

- [ ] B1 纳排一致性：`selfcheck` 的 `criteria_consistency` 无告警（每条 decision 与所引条款方向一致）。
- [ ] B2 证据质量：所有 `quote_verbatim` 检查为 PASS（引文是材料内逐字片段）。
- [ ] B3 返工量：检索式、筛选表、证据表、量表均存于 `/app/work`，他人可直接复用而无需重做。

## C. 创新增益（15 分）

- [ ] C1 结构化检索要素：研究问题被拆为 PICO/概念块并映射到布尔检索式。
- [ ] C2 自动第二评审：`selfcheck` 以规则引擎复核每条纳排决定，输出 `criteria_consistency`。
- [ ] C3 冲突证据矩阵：`synthesis.conflicts` 保留 `source_a/source_b/quote_a/quote_b/resolution`。

## D. 可靠体验（10 分）

- [ ] D1 触发正确：Skill 根目录执行 `python -B scripts/main.py --help` 退出码 0。
- [ ] D2 确定性：同一输入两次运行 `result.json` 与 `review.md` 字节一致（`selftest` 已验证）。
- [ ] D3 异常处理：空输入 / 坏 JSON / 坏编码 / 检索为空 / 全文不可得 / 证据矛盾均记录且退出码 0。

## E. 生态贡献（10 分）

- [ ] E1 文档齐全：`SKILL.md` 写清用途、适用条件、调用、输入输出、异常处理、自检。
- [ ] E2 资源可复用：检索式模板、纳排模板、筛选表、证据表、质量量表均可独立复用。
- [ ] E3 诚实标注：所有 `limitations` / `errors` / `degradations` / `pending_human` 如实填写。

## 确定性红线（本版特有）

- [ ] 结果内容不含墙钟时间、随机数、绝对输出路径或与顺序有关的字段。
- [ ] 证据 ID 由 `sha256(source_id, locator, quote)` 内容寻址生成，稳定可复算。
- [ ] 遥测（实际耗时、请求计数）只写 `/app/work/run_state.json`，不进入 `result.json`。
- [ ] 两次运行产物一致：提交前执行 `python -B scripts/selftest.py`，须 4/4 PASS。
