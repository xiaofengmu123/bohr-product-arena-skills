---
name: research-question-driven-search
description: >-
  科研问题驱动检索 Skill：把复杂科研问题拆为可检索子问题，规划查询顺序与依赖，
  受控扩展同义词/缩写/跨语言/上下位术语，多轮检索并全量筛选每一条记录，按透明规则排序，
  记录停止判断与缺口，交付可复核的 result.json 与结构化中间产物（plan.json / records.json / evidence.json）。
  适用于围绕科研问题检索、筛选、排序文献并保留筛选依据的场景；不适用于单篇精读、翻译或写作。
version: 1.1.0
---

# 科研问题驱动检索 Skill

## 1. 角色与何时使用

本 Skill 把“科研问题 → 拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付”固化为可重复流程。适用：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序依据、筛选账本与遗漏说明。不适用：单篇文献精读、全文翻译、写作润色、开放式科研问答。

核心纪律：**不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**。证据只能来自真实输入与真实检索/材料，读不出的字段写 `null`/`pending_human`，绝不示例填充。本题为有限快照：检索对象是题目给定范围内的可归因论文与查询输入，**不声称全局召回、实时数据库覆盖或人工评审**；重复导出/冲突观点若为输入夹具，按输入如实标注。

## 2. 输入契约

- 原始输入 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等论文原文快照）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig`→`utf-8`→`gb18030`→`latin-1` 逐级容错；坏 JSON、BOM、坏编码均降级不崩溃；单文件超过 2MB 跳过并记 `degradations`。
- 工作目录 `/app/work/`，最终产物写 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract` 等），其**全部字段必须原样回填**进 `result.json`：未填的给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 为强制）：

- `case_id`*（取自输入）、`status`*（`ok | partial | insufficient_evidence | input_error`）；
- 检索主结果 `results`*、`research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results` 每条含 `rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown`；
- `screening`*：**全量账本**（included/excluded/uncertain/merged_duplicates），排除项与未决定项**不静默丢弃**；
- `evidence`*：查询/轮次/冲突/缺口日志（query/source/database/timestamp/rationale/kind/round）；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*（time/action/reason/status）；
- 信封字段 `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本同时生成可编辑的 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询计划、术语扩展、停止判断）供人工复核。本任务无 Office/图件硬性要求，不生成 PDF/PNG。

## 4. 结构化中间产物（本版重点：中间表示与可复核）

脚本把检索过程写成**版本化、可独立复核的中间 JSON**，与最终 `result.json` 分离，任何一步都能回看“为什么这样拆、这样筛、这样停”：

| 文件 | 内容 | 生产命令 |
|-|-|-|
| `/app/work/case_summary.json` | 解析后的 case_id、研究问题、约束、`output_contract` 与材料清单 | `run` / `parse` |
| `/app/work/plan.json` | 六要素拆解、术语扩展、依赖化查询计划、停止判据与阈值 | `plan` / `set-plan` |
| `/app/work/records.json` | 规范化候选账本（含每条 screening 决定、locator+quote、score_breakdown） | `import` |
| `/app/work/evidence.json` | 逐轮查询/筛选/冲突/缺口日志 | `evidence` |
| `/app/work/change_log.json` | 增量操作留痕 | 各子命令自动追加 |

`assemble` 从这些中间产物**确定性**组装 `result.json`，因此同一组中间文件重复运行结果一致（除时间戳外）。

## 5. 执行流程（预算：总 900 秒、64 次模型请求、单回复 ≤16384 tokens）

先落盘骨架，再增量改进；**任何时刻中途失败，`/app/output/result.json` 都必须合法存在**。**前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架**，此后只用增量子命令更新，禁止重新 `run` 覆盖已填计划。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 -B scripts/main.py run` | /input/ | partial result.json + case_summary/plan | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间范围/比较/证据要求，形成 SQ1..SQn | case_summary | `set-plan`（sub_questions） | 2–4 分钟 |
| 2 规划 | 为子问题安排查询顺序与依赖（前置/并行） | sub_questions | `set-plan`（query_plan） | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位/领域变体，控制发散（每概念≤6 变体） | key_concepts | `set-plan`（term_expansion） | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选 + evidence | ≤25 分钟 |
| 5 筛选 | `import`：逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 | records.json | 随检索进行 |
| 6 收敛 | 检查子问题证据、冲突验证、边际收益，填 stop_decision | evidence | `set-plan`（stop_decision） | 3–5 分钟 |
| 7 组装 | `assemble` 写 result.json + screening.md + plan.md | 中间产物 | /app/output/ | ≤1 分钟 |
| 8 自检 | `main.py selfcheck` + `selftest.py` 逐条 PASS/FAIL | result.json | 自检报告 | ≤1 分钟 |

原则：脚本是确定性辅助，不消耗模型请求；64 次模型请求用于拆解、相关性判断与查询调整。建议时间分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每步完成后用 `assemble` 刷新 result.json，只做增量改进。

## 6. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：研究问题被拆成 ≥1 条可检索子问题，每条有对象/机制/方法/时间/比较/证据要求标注；每步输入输出清楚。
- [ ] **实际效果/正确性**：每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题均有 ≥1 条 included 证据；未覆盖的子问题列入 `uncertainty.coverage_gaps`。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询、筛选、排序与停止理由；`screening` 覆盖**全部**记录，排除项有理由。
- [ ] **使用增益**：多轮查询规划与术语扩展提高召回；全量筛选与透明打分减少人工返工；`main.py selfcheck` 全 PASS。
- [ ] **创新增益**：六要素拆解、依赖化查询规划、受控术语扩展、停止阈值与透明打分（0.6·relevance+0.2·元数据完整+0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验**：`--help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行结果一致（幂等）；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可按 `references/` 独立复现一次完整检索。

## 7. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口异常/限流/权限不足：记入 `errors`/`degradations`，改用本地材料补充并标注局限；无法继续时如实降级为 `partial`。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion` 注明歧义并加限定词，或拆成两条查询分别记录；无法判断的字段写 `pending_human`，不猜测。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出需人工补充的具体缺口。
- 检索词命中真实论文但元数据与来源冲突：保留逐字来源写法，冲突项写入 `evidence`（kind=conflict），不强制归一。

## 8. 脚本（纯标准库，相对路径调用）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用（路径为相对路径）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

复杂 JSON（拆解/候选/证据）优先用 `--file` 传文件，避免 shell 引号转义；`--inline` 仅用于简单单行。字段规范见 `references/result-schema.md`；方法细则与检查清单见 `references/method-and-checklist.md`。以上命令与字段均为示意，非正式案例结果。

## 9. 保底交付

若脚本无法运行，不要交零产物：读 `/input/case.json` 核对 `output_contract`，手写合法但可能不完整的 `/app/output/result.json`，据实写 `status`/`errors`/`degradations`；缺证据的结论写 `uncertainty`，不伪造日志、引文、分数。
