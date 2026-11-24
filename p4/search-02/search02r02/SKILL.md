---
name: question-driven-evidence-search
description: >-
  科研问题驱动检索 Skill：按对象/机制/方法/时间范围/比较/证据要求六要素把复杂科研问题拆成可检索子问题，
  制定带依赖与并行关系的查询计划及受控术语扩展，并依据“900 秒 / 64 次模型请求”的阶段预算推进多轮检索。
  硬性要求第 0 步在 3 分钟内先落盘可复核的 partial 结果骨架，此后只用增量子命令改进同一份 result.json；
  全程保留全量筛选账本、逐条证据溯源与透明打分排序。适用于需要围绕一个科研问题检索、筛选、排序文献并保留
  每一步筛选依据（而非单篇精读、全文翻译或写作润色）的场景。
version: 2.0.0
---

# 科研问题驱动检索 Skill

## 1. 角色与何时使用

本 Skill 把“科研问题 → 六要素拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付”固化为可重复流程。**适用**：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛序依据、证据溯源与遗漏说明。**不适用**：单篇文献精读、全文翻译、写作润色、开放式科研问答。

核心原则（不可违反）：**不编造文献、标题、作者、年份、期刊、DOI、URL、数值、来源或评测分数**；材料不足就写“材料不足 / not_reported / pending_human”，绝不用示例或臆测填充正式结果。脚本是确定性辅助，只落盘与组装 Agent 明确给出的内容，自身不做相关性判断。

## 2. 输入契约（先发现，再假设）

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料，即固定快照的论文原文）。**不假设固定键名**：先扫描 `/input/` 全部文件再解析。
- 文本按 `utf-8-sig` → `utf-8` → `latin-1` 顺序容错读取；坏 JSON、坏编码只降级不崩溃；单文件 >2MB 跳过并写入 `degradations`。
- `case.json` 若含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract`/`result_schema` 等键），其**全部字段必须原样回填**进 `result.json`：未获得的给 `null` 或 `[]`，不改名、不删未知键。
- `case_id` 优先取 `case.json` 的 `case_id`/`id`/`challenge_id`，其次取 `task.md` 中反引号包裹的 id；都缺失写 `not_reported` 并记入 `uncertainty.missing_metadata`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`（UTF-8 合法 JSON），至少含以下字段（`*` 强制）：

| 字段 | 含义 |
|-|-|
| `case_id`* / `status`* | 来自输入；`status ∈ ok \| partial \| insufficient_evidence \| input_error` |
| `research_question` | 从输入识别的研究问题 |
| `sub_questions`* | 六要素拆解，每条含 id/question/object/mechanism/method/time_range/comparison/evidence_requirement |
| `query_plan`* | 查询顺序与依赖（step/query/database/depends_on/parallel_with/rationale） |
| `term_expansion`* | 同义词/缩写/跨语言/上下位/领域变体 + 歧义标注 + 发散上限 |
| `stop_decision`* | 停止/收敛/转人工判断与理由 |
| `results`* | 纳入候选，每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown |
| `screening`* | **全量**筛选账本：included/excluded/uncertain/merged_duplicates 与 total_records，排除项不静默丢弃 |
| `evidence`* | 每轮查询/筛选/排序/停止动作日志：query/source/database/timestamp/rationale/kind |
| `sources`* / `uncertainty`* | 来源清单；missing_metadata/conflicts/coverage_gaps/notes |
| `change_log`* | time/action/reason/status |
| `status_notes`/`limitations`/`errors`/`degradations`/`pending_human`/`runtime` | 状态与异常说明 |
| 题目 `output_contract` 的全部字段 | 未获得的回填 `null`/`[]` |

脚本同时生成可编辑的 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解/查询计划/术语扩展/停止判断）。默认不生成 PDF/PNG/Office；若案例另行要求再按需产出真实渲染预览。

## 4. 执行流程（阶段预算：总 900 秒 / 64 次模型请求）

**硬性铁律**：第 0 步必须在开始后 3 分钟内落盘一版 `status=partial` 的合法 `result.json` 骨架；此后**只用增量子命令更新同一份文件，禁止重新 `run` 覆盖计划**。任何时刻中途失败，`/app/output/result.json` 都必须已存在且合法。

| 步骤 | 内容 | 输入 → 输出（命令） | 请求预算 | 时间预算 | 硬截止 |
|-|-|-|-|-|-|
| 0 骨架 | scaffold：发现输入→解析→写 partial 骨架 | `/input` → result.json(partial)+case_summary/plan | 0 | ≤3 分钟 | **3 分钟（硬性）** |
| 1 拆解 | 六要素识别，形成 SQ1..SQn | case_summary → set-plan(sub_questions) | 6–8 | 4 分钟 | 10 分钟 |
| 2 规划 | 查询顺序/依赖/并行 | sub_questions → set-plan(query_plan) | 4–6 | 3 分钟 | 15 分钟 |
| 3 扩展 | 受控术语扩展，每概念 ≤6 变体 | key_concepts → set-plan(term_expansion) | 4–6 | 3 分钟 | 20 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮 | 计划 → 候选记录 + evidence | 24–30 | 25 分钟 | 45 分钟 |
| 5 筛选 | 逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 → import(records) | 12–16 | 10 分钟 | 55 分钟 |
| 6 收敛 | 查子问题证据、冲突、边际收益 → stop_decision | evidence → set-plan(stop_decision) | 4–6 | 3 分钟 | 60 分钟 |
| 7 组装 | assemble 写 result.json + screening.md + plan.md | 全部 → /app/output/ | 0 | ≤1 分钟 | — |
| 8 自检 | selfcheck 逐条 PASS/FAIL | result.json → 清单 | 0 | ≤1 分钟 | — |

请求预算为 64 次，上表上限合计 ≤69 并预留缓冲：**每完成一步立即用 `assemble` 刷新产物**，若某步请求将超预算则提前写入 `stop_decision.status=handoff` 并把已完成部分固化为 partial。步骤 4 检索约占 40%，步骤 5 筛选约占 25%，拆解/规划/扩展占 20%，组装/自检/缓冲占 10%。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果·完成度(15)**：研究问题被拆成 ≥1 条可检索子问题，每条含对象/机制/方法/时间/比较/证据六要素标注；每步输入输出清楚。
- [ ] **实际效果·正确性(15)**：每条结果的标题/作者/年份/期刊/DOI/URL 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果·完整性(5)**：每个子问题均有 ≥1 条 included 证据；未覆盖的子问题写入 `uncertainty.coverage_gaps`。
- [ ] **实际效果·可核验性(5)**：`evidence` 记录每轮查询、筛选、排序与停止理由；`screening` 覆盖**全部**候选记录，排除项有理由。
- [ ] **使用增益(25)**：多轮查询规划 + 术语扩展提高召回；全量筛选 + 透明打分减少人工返工；`scripts/selftest.py` 与 `scripts/main.py selfcheck` 全部 PASS。
- [ ] **创新增益(15)**：六要素拆解、依赖化查询规划、受控术语扩展、停止阈值、透明打分（0.5·relevance + 0.3·元数据完整 + 0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验(10)**：`python3 -B scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；同输入重复运行结果确定（时间戳取固定快照文件 mtime）；异常写 errors/degradations。
- [ ] **生态贡献(10)**：SKILL.md + scripts + references 齐全，他人可按 `references/` 独立复现一次完整检索。

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议；**不补造文献**。
- 缺软件/接口异常/限流/权限不足：写入 `errors`/`degradations`，改用本地材料（materials/PDF）补充并标注局限；无法继续时如实降级为 `partial` 并说明。
- 元数据缺失：标 `null`/`not_reported` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion` 注明歧义并加限定词，或拆成两条查询分别记录；无法判断的字段写 `pending_human`，不猜测。
- 时间/请求将超预算：立即 `set-plan` 固化 `stop_decision.status=handoff`，`pending_human` 列出需人工补充的具体缺口，`assemble` 固化 partial 结果。

## 7. 脚本调用（纯标准库，相对路径，先 cd 到 skill 根目录）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用（路径为相对路径，保证可执行）：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py scaffold --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

复杂 JSON（拆解/候选/证据）一律用 `--file` 传文件避免 shell 转义。字段规范见 `references/result-schema.md`；方法细则、打分与检查清单见 `references/method-and-checklist.md`。以上命令与字段为接口示意，非正式案例结果。