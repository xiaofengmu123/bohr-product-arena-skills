---
name: research-question-driven-search
description: >-
  科研问题驱动检索 Skill：把复杂科研问题按“对象/机制/方法/时间范围/比较/证据要求”六要素拆成可检索子问题，
  做依赖化查询规划与受控术语扩展（每概念 ≤6 变体），多轮检索并逐条全量筛选全部记录（含排除/待定/合并重复），
  用 locator+逐字 quote 保留每条结论的证据，按透明公式 0.6·relevance+0.2·元数据+0.2·来源可靠 打分排序，
  记录停止判断与遗漏说明，交付可复核的 result.json。用于“围绕科研问题检索、筛选、排序文献并保留筛选依据”，
  不用于单篇精读、全文翻译、写作润色或开放式问答。
version: 2.0.0
---

# 科研问题驱动检索 Skill

## 1. 角色与何时使用

本 Skill 把“科研问题 → 六要素拆解 → 依赖化查询规划 → 受控术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付”固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇精读、全文翻译、写作润色、开放式问答。

**核心原则：不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**；无材料写“材料不足”，绝不示例填充。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`；可能另有 `materials/`（PDF 等固定快照论文原文）。实际输入与原文是事实依据，不依据标签或文件名补造数据。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig→utf-8→gb18030→latin-1` 容错降级，坏 JSON/坏编码不崩溃；单文件 >2MB 跳过并记入 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract` 等），其**全部字段必须原样回填**进 result.json：未获得的给 `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 为强制）：

- `case_id`*：取自输入；`status`*：`ok | partial | insufficient_evidence | input_error`；
- `research_question`、`sub_questions`*（六要素拆解）、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*：纳入候选，每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown；
- `screening`*：**全量筛选账本**（included/excluded/uncertain/merged_duplicates/unscreened），排除项不静默丢弃，`unscreened` 必须为空；
- `evidence`*：查询/轮次/冲突/缺口日志（kind/source/database/timestamp/query/rationale/result_count）；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*（time/action/reason/status）；
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本同时生成可编辑的 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询计划、术语扩展、停止判断），供人工复核。字段细则见 `references/result-schema.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求、每响应 ≤16384 token）

先落盘骨架再增量改进；**前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架，此后只用增量命令更新，禁止重跑 `run` 覆盖计划**。任何时刻中途失败，`result.json` 都必须合法存在。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ | result.json(partial) + case_summary/plan | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间范围/比较/证据要求，形成 SQ1..SQn | case_summary | set-plan（sub_questions） | 2–4 分钟 |
| 2 规划 | 为子问题安排查询顺序与依赖（前置/并行） | sub_questions | set-plan（query_plan） | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位变体，控制发散（每概念 ≤6 变体） | key_concepts | set-plan（term_expansion） | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选记录 + evidence | ≤25 分钟 |
| 5 筛选 | `scripts/main.py import --file`：逐条 include/exclude/uncertain/merged_duplicate + 理由 + locator/quote | 候选 | records.json | 随检索进行 |
| 6 收敛 | 检查每个子问题证据、冲突验证、边际收益，填 stop_decision | evidence | set-plan（stop_decision）+ set-meta | 3–5 分钟 |
| 7 组装 | `scripts/main.py assemble` 写 result.json + screening.md + plan.md | 全部 | /app/output/ | ≤1 分钟 |
| 8 自检 | `scripts/main.py selfcheck` 逐条 PASS/FAIL | result.json | 自检清单 | ≤1 分钟 |

原则：脚本是确定性辅助，不消耗模型请求；64 次模型请求用于拆解、相关性判断与查询调整。建议：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每步完成后用 `assemble` 刷新 result.json，只做增量改进。

## 5. 评分维度对齐表（本版核心设计）

| 评分维度（分值） | 对应流程步骤 | 对应自检项 |
|-|-|-|
| 实际效果·完成度（15） | 步骤 0–3 六要素拆解与规划 | S1：≥1 条子问题且六要素标注齐全 |
| 实际效果·正确性（15） | 步骤 4–5 逐字保留来源写法 | S2：标题/作者/年份/期刊/DOI 真实；证据为 locator+quote |
| 实际效果·完整性（5） | 步骤 5–6 覆盖追踪 | S3：每子问题 ≥1 条 included；缺口入 coverage_gaps |
| 实际效果·可核验性（5） | 步骤 4–6 日志与账本 | S4：evidence 记录每轮；screening 覆盖全部记录 |
| 使用增益（25） | 多轮规划、术语扩展、全量筛选、透明打分 | S5：selfcheck 全 PASS；公平对照由平台白板给出 |
| 创新增益（15） | 六要素、依赖化规划、受控扩展、停止阈值、透明打分 | S6：方法由脚本固化、可复现，非仅文字自述 |
| 可靠体验（10） | 降级与幂等设计 | S7：`--help` 退出 0；空输入/坏 JSON/坏编码不崩溃；重复运行语义幂等 |
| 生态贡献（10） | SKILL.md + scripts + references | S8：他人可按 references 独立复现一次完整检索 |

## 6. 自检清单（逐条可勾选）

- [ ] S1 实际效果/完成度：子问题 ≥1 且每条有对象/机制/方法/时间/比较/证据要求标注；每步输入输出清楚。
- [ ] S2 实际效果/正确性：结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；相关性依据为 locator+quote。
- [ ] S3 实际效果/完整性：每个子问题 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`。
- [ ] S4 实际效果/可核验性：`evidence` 记录每轮查询、筛选、排序与停止理由；`screening` 覆盖全部记录，排除项有理由。
- [ ] S5 使用增益：多轮规划与术语扩展提高召回；全量筛选与透明打分减少返工；`selfcheck` 全 PASS。
- [ ] S6 创新增益：六要素、依赖化规划、受控扩展、停止阈值与透明打分由脚本固化、可复现。
- [ ] S7 可靠体验：`--help` 退出 0；空输入/坏 JSON/坏编码/超大文件不崩溃且退出 0；重复运行语义幂等；异常写 errors/degradations。
- [ ] S8 生态贡献：SKILL.md + scripts + references 齐全；`selftest.py` 覆盖 8 类场景且退出 0。

## 7. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议，不补造文献。
- 缺软件/接口异常/限流/权限不足：记录到 `errors`/`degradations`，改用本地 `materials/` 补充并标注局限；无法继续时如实降级 `partial` 并说明。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion` 注明歧义并加限定词或拆成两条查询分别记录；无法判断的字段写 `pending_human`。
- 重复导出：合并为 `merged_duplicate` 并保留来源；冲突意见：各自保留并标 `conflict=true`，绝不二选一。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出需人工补充的具体缺口。

## 8. 脚本调用（相对路径，纯标准库）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py set-meta --file /app/work/meta_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

复杂 JSON 优先用 `--file` 传文件，避免 shell 引号转义；`--inline` 仅用于简单单行。`main.py` 为薄入口（<30KB），实现在 `scripts/core_impl.py`。字段规范见 `references/result-schema.md`；方法与检查清单见 `references/method-and-checklist.md`。

## 9. 边界与限制

- 一个 Skill 只解决“科研问题驱动检索”一类任务，不做通用大而全。
- 有限快照：不声称实时数据库、全库召回或人工复核；只覆盖案例给定材料与当轮检索记录。
- 没有真实材料时输出“材料不足”，不示例填充；正式测试案例、白板与评测结果由平台提供，本包不预写答案。
