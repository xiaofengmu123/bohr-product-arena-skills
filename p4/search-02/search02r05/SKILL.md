---
name: research-question-driven-search
description: >-
  科研问题驱动检索 Skill：把复杂科研问题拆解为可检索子问题，规划查询顺序与依赖，
  扩展同义词/缩写/跨语言/上下位术语，多轮检索并逐条筛选全部记录，按透明规则排序，
  记录停止判断、遗漏与冲突，交付字段与 output_contract 逐一对齐、可复核的 result.json。
  当需要围绕一个科研问题检索、筛选、排序文献并保留筛选依据（而非单篇精读、翻译或写作）时使用。
version: 1.1.0
---

# 科研问题驱动检索 Skill

## 1. 角色与何时使用

本 Skill 把「科研问题 → 子问题拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付」固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇文献精读、全文翻译、写作润色、开放式科研问答。

核心原则：**不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**；无材料就写「材料不足」，绝不示例填充。三条判对标准：问题被拆成哪些可检索子问题、每个子问题是否找到足够证据、检索过程能否解释与复现。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料，即固定快照的论文原文）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig`/`utf-8` 读取并容错（BOM、坏编码、坏 JSON 降级不崩溃）；单文件 >2MB 跳过并记入 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- `case.json` 中 `output_contract`（或 `output_schema`/`required_fields`/`contract`/`fields` 等别名）的**全部字段必须原样回填**进 result.json：缺值给 `null`，不改名、不删未知键；完整契约同时存入 `output_contract`，字段名清单存入 `output_contract_fields`。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 强制）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）
- `research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*
- `results`*：每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown
- `screening`*：**全量账本**（included/excluded/uncertain/merged_duplicates），排除项不静默丢弃
- `evidence`*：查询/轮次/冲突/缺口日志（query/source/database/timestamp/rationale/kind）
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*（time/action/reason/status）
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`；`output_contract` 与 `output_contract_fields` 记录溯源）

脚本同时生成可编辑 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询计划、术语扩展、停止判断），供人工复核。本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求，再按需产出。

## 4. 执行流程（总预算：900 秒、64 次模型请求）

先落盘骨架，再增量改进；**任何时刻中途失败，result.json 都必须合法存在**。**前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架，此后只用增量子命令更新同一份 result.json，禁止重新 `run` 覆盖计划**（脚本对已有工作的 re-run 自动拒绝，除非 `--force`）。

| 步骤 | 内容 | 输入 | 输出 | 预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 -B scripts/main.py run`（扫描→解析→空规划→组装） | /input/ | result.json(partial)+case_summary+plan | ≤3 分钟（硬性） |
| 1 拆解 | 六要素：对象/机制/方法/时间范围/比较/证据要求，形成 SQ1..SQn | case_summary | `set-plan`（sub_questions） | 2–4 分钟 |
| 2 规划 | 子问题查询顺序与依赖（前置/并行） | sub_questions | `set-plan`（query_plan） | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位/领域变体，每概念 ≤6 变体 | key_concepts | `set-plan`（term_expansion） | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮 | plan | 候选记录 + evidence | ≤25 分钟 |
| 5 筛选 | `python3 -B scripts/main.py import --file`：逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 | records | 随检索进行 |
| 6 收敛 | 子问题证据覆盖、冲突验证、边际收益，填 stop_decision | evidence | `set-plan`（stop_decision） | 3–5 分钟 |
| 7 对齐 | `python3 -B scripts/main.py set-fields --file`：把结果映射到 output_contract 字段 | results+contract | result_extra | ≤2 分钟 |
| 8 组装 | `python3 -B scripts/main.py assemble` 写 result.json+screening.md+plan.md | 全部 | /app/output/ | ≤1 分钟 |
| 9 自检 | `python3 -B scripts/main.py selfcheck` 逐条 PASS/FAIL | result.json | 自检清单 | ≤1 分钟 |

原则：脚本是确定性辅助，不消耗模型请求；64 次请求用于拆解、相关性判断与查询调整。建议分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每步完成后用 `assemble` 刷新 result.json，只做增量改进。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：拆成 ≥1 条可检索子问题，每条标注对象/机制/方法/时间/比较/证据要求；每步输入输出清楚。
- [ ] **实际效果/正确性**：每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询、筛选、排序与停止理由；`screening` 覆盖**全部**记录，排除项有理由。
- [ ] **使用增益**：多轮查询规划与术语扩展提高召回；全量筛选与透明打分减少人工返工；`selfcheck` 全 PASS。
- [ ] **创新增益**：六要素拆解、依赖化查询规划、受控术语扩展、停止阈值与透明打分（0.6·relevance+0.2·元数据完整+0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验**：`--help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行不覆盖已有工作（幂等）；异常写入 errors/degradations；`scripts/selftest.py` 全 PASS。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可按 `references/` 说明独立复现一次完整检索。

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口异常/限流/权限不足：记入 `errors`/`degradations`，改用本地材料（materials/PDF）补充并标注局限；无法继续时如实降级为 partial 并说明。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion` 注明歧义并加限定词，或拆成两条查询分别记录；无法判断的字段写 `pending_human`，不猜测。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出需人工补充的具体缺口。

## 7. 脚本（纯标准库，相对路径调用）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用（路径为相对路径，保证可执行）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work --output /app/output
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work --output /app/output
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work --output /app/output
python3 -B scripts/main.py set-fields --file /app/work/fields_in.json --work /app/work --output /app/output
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

复杂 JSON（拆解/候选/证据/契约映射）优先用 `--file` 传文件，避免 shell 引号转义；`--inline` 仅用于简单单行。字段规范见 `references/result-schema.md`；方法细则与检查清单见 `references/method-and-checklist.md`。