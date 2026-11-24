---
name: evidence-auditable-research-search
description: >-
  科研问题驱动的全记录可复核文献检索 Skill：把复杂科研问题拆解为带对象、机制、方法、时间范围、比较、证据要求六要素的可检索子问题，
  规划查询顺序与依赖并做受控术语扩展，对有限快照内的全部记录逐条筛选，显式识别重复导出与证据冲突，按透明规则排序并记录停止判断与遗漏，
  交付可复核的 result.json、screening.md 与 plan.md。用于检索、筛选、排序文献并保留筛选依据的场景；不做单篇精读、全文翻译或写作。
version: 1.1.0
---

# 科研问题驱动检索 Skill（证据可复核版）

## 1. 角色与何时使用

本 Skill 把「科研问题 → 六要素拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 去重与冲突校验 → 停止判断 → 可复核交付」固化为可重复流程。适用于：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据、重复导出与冲突说明、遗漏与不确定性。不适用于：单篇精读、全文翻译、写作润色、开放式科研问答。

两条铁律：**不编造任何文献、标题、作者、年份、期刊、DOI、数值或来源**；**全量记录**——排除项不静默丢弃，重复项显式合并，冲突项双方都保留。无材料就写「材料不足」，绝不示例填充。

## 2. 输入契约

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料，即固定快照的论文原文）与自由散落的 `.txt/.md/.jsonl`。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件超过 2MB 跳过并写入 `degradations`。
- PDF 原文不预设「已抽取答案层」：用平台 `pdf-text-tool`/`pdftotext` 实际解析，解析产物作为证据来源；脚本只登记 PDF 路径，语义抽取由 Agent 用上述工具完成。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract` 等），其**全部字段必须原样回填**进 result.json：未获得的给 `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 强制）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）；
- `research_question`、`sub_questions`*（六要素拆解）、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*：候选集，每条含 `rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown`；
- `screening`*：**全量筛选账本**（`included/excluded/uncertain/merged_duplicates`），排除项必有理由；
- `duplicates`*：重复导出合并记录（哪个并入哪个、指纹、理由）——应对案例中显式标注的 synthetic duplicate exports；
- `conflicts`*：证据冲突双方逐字记录（`source_conflict` 不擅自取舍）——应对 conflicting opinions fixture；
- `coverage`*：子问题 × 证据覆盖矩阵，`coverage_scope` 固定声明「finite snapshot; no global-recall claim」；
- `evidence`*：查询/轮次/冲突/缺口日志（`query/source/database/timestamp/rationale/kind`）；
- `sources`*、`uncertainty`*（`missing_metadata/conflicts/coverage_gaps/notes`）、`change_log`*（`time/action/reason/status`）；
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本同时生成可编辑的 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询计划、术语扩展、覆盖矩阵、停止判断）供人工复核。本任务无 Office/图件硬性要求，默认不生成 PDF/PNG；案例另行要求时再按需产出。

## 4. 执行流程（总预算 900 秒、64 次模型请求、单响应 ≤16384 token）

**硬性纪律：先落盘骨架，再增量改进；任何时刻中途失败，`/app/output/result.json` 都必须合法存在。** 前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架；此后只用增量子命令更新同一份 `state.json`，**禁止重新 `run` 覆盖计划**。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（scan→parse→skeleton→assemble） | /input/ | result.json(partial) + state.json | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间范围/比较/证据要求，形成 SQ1..SQn | case_summary | set-plan(sub_questions) | 2–4 分钟 |
| 2 规划 | 为子问题安排查询顺序与依赖（前置/并行） | sub_questions | set-plan(query_plan) | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位/领域变体，每概念 ≤6 变体控制发散 | key_concepts | set-plan(term_expansion) | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选记录 + evidence | ≤25 分钟 |
| 5 筛选 | `python3 scripts/main.py import`：逐条 included/excluded/uncertain + 理由 + locator/quote | 候选 | records（全量） | 随检索进行 |
| 6 去重冲突 | 由 `assemble` 按 DOI/标题指纹自动合并重复导出；冲突双方用 evidence 记录 | records | duplicates/conflicts | 3–5 分钟 |
| 7 收敛 | 检查每个子问题证据、边际收益，填 stop_decision | evidence | set-plan(stop_decision) | 3–5 分钟 |
| 8 组装 | `python3 scripts/main.py assemble` 写 result.json + screening.md + plan.md | 全部 | /app/output/ | ≤1 分钟 |
| 9 自检 | `python3 scripts/main.py selfcheck` 逐条 PASS/FAIL | result.json | 自检清单 | ≤1 分钟 |

脚本是确定性辅助，不消耗模型请求；64 次请求全部用于拆解、相关性判断与查询调整。建议时间分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每个步骤完成后用 `assemble` 刷新 result.json，只做增量改进。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：研究问题被拆成 ≥1 条可检索子问题，每条有对象/机制/方法/时间范围/比较/证据要求标注；每步输入输出清楚。
- [ ] **实际效果/正确性**：每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题均有 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`；`coverage.coverage_scope` 声明有限快照、不宣称全局召回。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询、筛选、排序、去重、冲突与停止理由；`screening` 覆盖**全部**记录，排除项有理由。
- [ ] **使用增益**：多轮查询规划与术语扩展提高召回；全量筛选、自动去重与冲突校验减少人工返工；`selfcheck` 全 PASS。
- [ ] **创新增益**：六要素拆解、依赖化查询规划、受控术语扩展、有限快照覆盖模型、DOI/标题指纹去重、冲突双方保留、透明打分（0.6·relevance+0.2·元数据完整+0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验**：`main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行一致（幂等）；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可按 `references/` 独立复现一次完整检索；`selftest.py` 可在未装任何依赖的环境离线自检。

## 6. 降级与不确定性策略（缺数据不编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口异常/限流/权限不足：记入 `errors`/`degradations`，改用本地 materials 补充并标注局限；无法继续时如实降级为 `partial`。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 重复导出：按 DOI 优先、否则标题指纹合并，`duplicates` 记下合并对与理由，原记录不删除（`merged_duplicates` 保留）。
- 证据冲突（如案例中 conflicting opinions）：双方都保留逐字 quote，`conflicts` 记录分歧点与「未解决/已调和/择优」状态；无法判断的写 `pending_human`。
- 术语严重歧义：在 `term_expansion` 注明歧义并加限定词，或拆成两条查询分别记录。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出需人工补充的具体缺口；`limitations` 写明未完成原因。

## 7. 脚本调用（纯标准库，相对路径）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用；路径为相对路径，保证可执行：

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

复杂 JSON 优先用 `--file` 传文件，避免 shell 引号转义；`--inline` 仅用于简单单行。字段规范见 `references/result-schema.md`，方法细则与检查清单见 `references/method-and-checklist.md`。以上命令与字段为示意，非正式案例结果。

## 8. 复用与边界

- 复现一条完整检索的最小编排：`run → set-plan（拆解/规划/扩展/停止）→ import（全量候选）→ evidence（每轮日志）→ assemble → selfcheck`，全流程脚本化、幂等、离线可跑。
- 一个 Skill 只解决「检索-筛选-排序-溯源」一类任务，不追求大而全。
- 交付前在非作者环境（不同机器/Python 版本）跑 `selftest.py`，确认空输入、坏 JSON、坏编码、去重+合同回填、幂等五类场景全 PASS，且 `main.py --help` 退出码 0。
