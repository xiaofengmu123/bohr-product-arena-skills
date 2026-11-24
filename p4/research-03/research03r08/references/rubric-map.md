# 评分维度 → 流程节点 → 自检项对齐表

本 Skill 的明确设计取舍是**评分维度对齐**：题目五个评分维度不是并列清单，
而是被逐条映射到流程环节、落盘产物和 `selfcheck` 检查项。任何一步只做“能回
到某个评分维度”的事，杜绝写空泛总结。

## 1. 映射总表

| 评分维度（分值） | 关键评分点 | 本 Skill 的流程环节 | 落盘产物 | selfcheck 检查项 |
|-|-|-|-|-|
| 实际效果（40） | 任务完成度 / 正确性 / 完整性 / 可核验性 | P1 问题拆解与协议 → P2 两轮筛选 → P3 质量与证据 → P4 综合收尾 | protocol.json / screening.jsonl / quality_appraisal.jsonl / evidence_cards.jsonl / synthesis.json | review_question, subquestions, search_strategy, inclusion, exclusion, screening, prisma, quality, evidence_backlink, conflicts, gaps |
| 使用增益（25） | 质量增益 / 效率增益 / 增益可验证 | 事先条款 → 逐条判定 → 计数可重算 → 冲突并列 | 纳排标准模板 + 筛选记录 + PRISMA 计数 | screening（exclude 必有 reason+criterion_id）, prisma（可重算） |
| 创新增益（15） | 新增能力 / 方法改进 / 创新证据 | 检索式结构化（database+query+intent）、纳排版本化、证据自动回链、冲突矩阵 | search_strategy.query_blocks / evidence_map / synthesis.conflicts | search_strategy, evidence_backlink, conflicts |
| 可靠体验（10） | 调用易用 / 运行稳定 / 异常处理 | 骨架先落盘 → 增量改进 → 坏输入降级 | run_state.json / change_log / errors / degradations | parse, status, case_id, honest_fields |
| 生态贡献（10） | 文档完整 / 资源复用 / 推广价值 | 模板化量表、检索式模板、筛选表、证据表 | references/ 下的量表与模板 + review.md | （人工评审，脚本不判） |

## 2. 逐维度的落地约定

### 实际效果（40 分）

- **问题拆解**：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且写清依赖顺序。
- **检索可执行**：每个 `query_blocks` 必须含 `database`、`query`（布尔/字段/受控词）、
  `intent`，并显式写时间与语言限制；缺一即判不合格。
- **纳排判定符合事先条款**：筛选决定先于记录，`exclude` 必须回指 `criterion_id`。
- **证据不越界**：结论只允许引用 `evidence_map` 中已存在的 `evidence_id`；`quote` 必须逐字。
- **六方面覆盖**：结论覆盖“问题拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白”，
  缺项时 `status` 不能为 `ok`。

### 使用增益（25 分）

- **一致性**：同一份输入复跑，纳入集由确定性脚本从 `screening.jsonl` 重算，不依赖记忆。
- **减返工**：检索式模板、纳排标准、筛选表、证据表全部可复制到下一个案例复用。
- **可验证**：`selfcheck` 输出 `passed/total/failed` 计数与逐项明细，增益可被第三方复测。

### 创新增益（15 分）

- **检索式结构化**：不允许退化成关键词串；`query_blocks` 强制三段（库/式/意图）。
- **纳排版本化留痕**：`change_log` 记录每次 `merge/finalize`，人工改条款要写 `action=criteria_update`。
- **证据自动回链**：`finalize` 把 `evidence_cards.jsonl` 组装进 `evidence_map`，结论逐条回链。
- **冲突证据矩阵**：`synthesis.conflicts` 每条必须并列两个来源，无法裁定标 `undetermined`。

### 可靠体验（10 分）

- **触发与调用**：`SKILL.md` 写清“何时使用/不使用”，命令全部相对路径。
- **不同规模稳定**：脚本流式读 JSONL，不把全部材料一次性载入内存，数千条题录可跑。
- **异常处理**：检索为空、全文不可得、术语歧义、条款冲突、证据矛盾、权限不足，
  全部记入 `errors`/`degradations`/`pending_human`，进程退出码保持 0。

### 生态贡献（10 分）

- 纳排标准、质量量表、筛选表、证据表均为独立 JSON 模板，可被他人直接复用；
  `references/output-contract.md` 给出字段定义与量表等级，不依赖私有约定。

## 3. 与本 Skill 各文件的关系

| 文件 | 承载的评分维度 |
|-|-|
| SKILL.md | 全部（触发、契约、预算、自检、降级、边界） |
| scripts/main.py | 实际效果（组装）、可靠体验（骨架/异常）、使用增益（可重算） |
| scripts/resultlib.py | 可靠体验（编码/JSON 降级、确定性写盘） |
| scripts/selftest.py | 可靠体验（空输入/坏 JSON/坏编码/正常链路四类回归） |
| references/output-contract.md | 生态贡献（字段定义、量表、模板） |
| references/rubric-map.md | 全部（本表，评分维度对齐的声明与落地） |
