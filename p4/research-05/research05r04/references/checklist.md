# 交付前自检清单（references/checklist.md）

按评分维度组织，逐条勾选；勾选状态必须与 `result.json` 的
`status/errors/degradations/pending_human/limitations` 一致。可配合
`python3 -B scripts/selftest.py` 做机器自检。

## 实际效果（40 分：任务完成 15 / 正确 15 / 完整 5 / 可核验 5）

- [ ] `research_question.testable_statement` 含 对象/变量/基线/预期/可证伪条件。
- [ ] `prior_work.comparison_matrix` 五列（任务/方法/数据/评价/结论）齐全，
      相近工作识别正确，`incomparability_reason` 已标注，未跨文档串值。
- [ ] `novelty.claims` 每条有 `type` + `verdict` + `covered_by`；
      场景迁移/结论修正未误判为方法创新。
- [ ] `feasibility.premises` 每条有 `reported|assumed|missing|conflicting`，
      `assumed` 显式标注；给出 `conditional_conclusion` 与 `failure_risks`。
- [ ] 六类内容覆盖：问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白。
- [ ] `search_protocol` 含检索式 + 纳排规则，可独立复核。
- [ ] 每条关键结论在 `evidence_matrix` 有 `locator` + 逐字 `quote`。

## 使用增益（25 分：质量 15 / 效率 5 / 增益可验证 5）

- [ ] 结论推进到可决策：能直接回答“增量在哪/能否做成/以什么为前提”。
- [ ] `evidence_matrix` + `report.md` + `change_log` 三处一致，无需人工返工即可复核。
- [ ] 增益可复核：证据定位与判定理由在文中可回查。

## 创新增益（15 分：新增能力 5 / 方法改进 5 / 创新证据 5）

- [ ] 形成基础 Agent 难以稳定的能力：结构化四项交付 + 证据回链 + 冲突登记。
- [ ] 判定口径（拆解/比较维度/新颖性/可行性）在 `references/assessment-rules.md` 写明。
- [ ] 新增判断的边界与价值在 SKILL.md 第 1 节说明（适用/不适用）。

## 可靠体验（10 分：调用 3 / 稳定 4 / 异常 3）

- [ ] 触发路径清楚：读 SKILL.md → `scripts/main.py skeleton` → `evidence` →
      写 fragments → `finalize`。
- [ ] 前 3 分钟已落盘 partial 骨架，此后只做增量改进。
- [ ] 空输入/坏 JSON/缺软件/写盘失败均有降级，退出码 0（finalize 校验失败除外），
      不崩溃、不编造。
- [ ] 重复运行同一输入结果一致；`change_log` 记录每一步改动。

## 生态贡献（10 分：文档 3 / 复用 4 / 分享 3）

- [ ] 输入契约、评估口径、依赖、适用范围、限制、维护信息齐全。
- [ ] `core_impl.py` / `main.py` 只用标准库，比较矩阵与证据表模板可复用。
- [ ] 说明与提交版本一致，无示例答案冒充正式案例结果。

## 本版强化项（降级与不确定性）

- [ ] `limitations` 与 `uncertainty` 分离：前者记方法/输入限制，后者记结论不确定。
- [ ] 每条 limitation 有 `cause/severity/mitigation/affects`。
- [ ] 每条 uncertainty 有 `kind/claim/reason/effect_on_conclusion/resolution`。
- [ ] “未报告”一律标 `missing`，不写成“不存在”；无法核验标 `unverifiable`。
- [ ] 缺数据时字段保持 `null/[]` 并写入 `uncertainty.gaps`，status 如实降级。
- [ ] 不编造 DOI、数值、分数、运行结果。

## 快速命令

```bash
python3 -B scripts/main.py --help                 # 退出码 0
python3 -B scripts/selftest.py                    # 自检全绿，退出码 0
# 正式运行（在 /skills/weekly-skill 下）：
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py evidence --input /input --output /app/output --work /app/work
# …… Agent 写入 /app/work/fragments/*.json ……
python3 -B scripts/main.py finalize --input /input --output /app/output --work /app/work
```
