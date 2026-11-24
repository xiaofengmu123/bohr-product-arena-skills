# 评分维度映射与自检清单（02_scoring_checklist）

把题目 5 个评分维度逐条映射到本 Skill 的流程动作与可勾选自检项，供交付前核对。分值引用自题面，仅作对齐用，不构成对正式评测结果的断言。

## 1. 实际效果（40 分）→ P1/P2/P3 三段交付
- 任务完成度（15）：是否真正落到"论断清单—证据表—成稿"三段，而非只给一份成稿。
  - [ ] `/app/output/result.json` 含 `claims`、`sources`、`deliverable` 且均非空（或如实标 insufficient_evidence）。
  - [ ] 成稿满足题目指定体裁、受众与格式（deliverable.md 或按 contract 指定格式）。
- 结果正确性（15）：引用真实、元数据准确、原文真的支持论断。
  - [ ] 每条 source 的 `quote` 为逐字摘录，可回原文比对。
  - [ ] 每条 claim 的 `support` 级别与 quote 内容一致（不强写为 supports）。
  - [ ] `metadata` 中作者/年份/DOI/卷期只写材料或检索结果里出现的值。
- 结果完整性（5）：论断、来源、证据强度、不确定性四要素齐备。
  - [ ] 每条 claim 有 `claim_id/text/evidence_strength/strength_reason/uncertainty/sources`。
  - [ ] 每条 source 有 `source_id/locator/quote/claim_ids/support/verification`。
- 结果可核验性（5）：编号、摘录、定位、强度理由可独立复核。
  - [ ] claim_id/source_id 全局唯一且成稿内联标号 `[Cn]` 与 claims 一一对应。
  - [ ] locator 统一格式，quote 与定位可指回原文同一处。

## 2. 使用增益（25 分）→ 一次给全 + 可追溯记录
- 质量增益（15）：相比"只写一篇稿"的基础 Agent，是否减少弱证据写成强结论、无来源论断、隐藏冲突。
  - [ ] 每条论断在成稿中的措辞与其 evidence_strength 相称（对照 01 口径表）。
  - [ ] 冲突证据已并列记录，未单方面隐藏。
- 效率增益（5）：减少回原文核对、补引用、改写过度推断段落的人工返工。
  - [ ] 证据表一次含 quote + locator，无需作者再回原文找出处。
  - [ ] uncertainty 直接给出可粘贴进成稿的限定语。
- 增益可验证性（5）：同稿同版本同口径记录在 sources/change_log。
  - [ ] change_log 记录 scaffold/extract/match/grade/draft/finalize 各动作与计数。
  - [ ] 同一输入重复运行结果一致（scaffold 不覆盖非 in_progress 版本）。

## 3. 创新增益（15 分）→ 证据侧结构级产物
- 新增能力（5）：论断切分 + 来源匹配 + 强度判定，超越"有引用即通过"。
  - [ ] 采用 `claim_type`（含 causal/consensus 区分）而非平铺句子。
- 方法改进（5）：证据强度四级口径 + 多对多 claim↔source 双向索引。
  - [ ] evidence_strength 四级齐备且逐条给 strength_reason。
- 创新证据（5）：定位到段落/句子的 locator + 逐字 quote 使复核可重跑。
  - [ ] scripts/selftest.py 全 PASS，结构自检可独立重跑。

## 4. 可靠体验（10 分）→ P0 先落盘 + 异常识别
- 调用与易用性（3）：SKILL.md 写清触发条件、调用方式、输入输出、异常处理。
  - [ ] frontmatter 有 name/description/version；脚本用相对路径可执行。
- 运行稳定性（4）：不同学科、不同文献可得性、重复运行持续产出。
  - [ ] 前 3 分钟先落盘 in_progress 骨架，之后增量改进，重跑不丢结果。
  - [ ] 空输入/坏 JSON/缺材料三种情况各跑一次 selftest 均不崩溃。
- 异常处理（3）：文献不可得、仅摘要、原文与摘要矛盾、证据冲突、格式不支持、权限不足的识别与处置。
  - [ ] 每类异常都有对应记录位置：limitations/errors/degradations/uncertainty/pending_human。

## 5. 生态贡献（10 分）→ 文档与复用
- 文档完整性（3）：使用说明、依赖、配置、示例、适用范围、限制、强度口径、维护信息齐备。
  - [ ] SKILL.md + references 覆盖上述全部；依赖为"仅 Python 标准库"。
- 资源可复用性（4）：Skill 本体、脚本、论断切分模板、证据表结构、题集可被他人复用。
  - [ ] 01_evidence_framework 提供可复用的 claims/sources schema 与强度口径。
  - [ ] main.py/scaffold.py/selftest.py 与具体案例解耦，靠 /input、/app/work、/app/output 约定复用。
- 分享推广价值（3）：沉淀可分享的失败类型、核验方法、真实写作价值。
  - [ ] 降级策略列明材料缺口/冲突/不可回溯的处置，可作为失败类型模板复用。

## 6. 交付前终检
```bash
python scripts/scaffold.py --case /input/case.json --out /app/output --work /app/work
python scripts/main.py assemble --case /input/case.json --work /app/work --out /app/output
python scripts/main.py validate --out /app/output
python scripts/selftest.py
```
全 PASS 且 `result.json` 含 case_id + contract 全部键 + status/sources/uncertainty/change_log/limitations 后，方可交付。
