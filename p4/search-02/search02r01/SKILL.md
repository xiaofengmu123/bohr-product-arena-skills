---
name: research-question-evidence-search
description: >-
  科研问题驱动检索 Skill（证据溯源强化版）：把复杂科研问题拆解为可检索子问题，
  规划查询顺序与依赖，受控扩展同义词/缩写/跨语言/上下位术语，多轮检索并全量筛选记录，
  按透明规则打分排序；每条结论强制绑定 locator + 逐字 quote，脚本对本地材料做规范化
  quote 命中校验并输出 evidence_map（by_claim/by_result/by_sub_question + 孤儿结论 +
  未命中引文），记录停止判断与遗漏说明，交付可复核的 result.json。用于围绕科研问题
  检索、筛选、排序文献并保留完整证据链时使用；不负责单篇精读、翻译或写作。
version: 2.1.0
---

# 科研问题驱动检索 Skill（证据溯源强化版）

## 1. 角色与何时使用

把“科研问题 → 六要素拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 证据溯源核验 → 停止判断 → 可复核交付”固化为可复现流程。适用：围绕研究对象/机制/方法/时间范围/比较关系/证据要求检索文献，交付候选集、排序与筛选依据、遗漏说明及逐字证据链。不适用：单篇精读、全文翻译、写作润色、开放式问答。

核心原则：**不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**；无材料就写“材料不足”，绝不示例填充。本版强化点：**每条结论（claim）都必须有 locator + 逐字 quote**，脚本对本地材料逐字命中做规范化校验，未命中/无引用的结论单列，不静默丢弃。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 或已提取文本的固定快照）。PDF 由平台提供的解析工具提取，脚本只核验文本类材料，不做 PDF 解析。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 > 2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或其别名 `output`/`schema`/`required_fields`/`contract`），其**全部字段必须原样回填**进 result.json：未填的给 `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）、`status_notes`；
- `research_question`、`sub_questions`*（六要素 facets：object/mechanism/method/time_window/comparison/evidence_requirement）、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*：每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence/score/score_breakdown；
- `screening`*：全量账本（included/excluded/uncertain/merged_duplicates），排除项不静默丢弃；
- `evidence`*：查询/筛选/排序/冲突/缺口日志（evidence_id/claim_id/claim_text/kind/source_ref/locator/quote/quote_hash/source/timestamp/confidence/local_verified）；
- `evidence_map`*：`by_claim`/`by_result`/`by_sub_question` + `orphan_claims` + `unverified_quotes` + `verification` 统计；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*、`pending_human`、`errors`、`degradations`、`limitations`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本同时生成可编辑的 `/app/output/screening.md`、`/app/output/plan.md`、`/app/output/evidence_map.md` 供人工复核。字段细则见 `references/result-schema.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求、单响应 16384 token）

先落盘骨架，再增量改进；**任何时刻中途失败，`/app/output/result.json` 都必须合法存在**。**前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架**，此后只用增量子命令更新同一份 state，禁止重新 `run` 覆盖计划。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（扫描→骨架→assemble） | /input/ | result.json(partial)+摘要 | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间范围/比较/证据要求 → SQ1..SQn | 摘要 | `set-plan`(sub_questions) | 2–4 分钟 |
| 2 规划 | 为子问题排查询顺序与依赖（前置/并行） | sub_questions | `set-plan`(query_plan) | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位/领域变体，每概念 ≤6 变体 | key_concepts | `set-plan`(term_expansion) | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选 + evidence | ≤25 分钟 |
| 5 筛选 | `import --file`：逐条 include/exclude/uncertain + 理由 + locator+quote | 候选 | records+evidence_map | 随检索进行 |
| 6 收敛 | 检查子问题证据、冲突验证、边际收益 → `set-stop` | evidence | stop_decision | 3–5 分钟 |
| 7 组装 | `assemble` 写 result.json+screening.md+plan.md+evidence_map.md | 全部 | /app/output/ | ≤1 分钟 |
| 8 自检 | `selfcheck` 逐条 PASS/FAIL | result.json | 自检清单 | ≤1 分钟 |

脚本是确定性辅助，不消耗模型请求；64 次请求用于拆解、相关性判断与查询调整。建议：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每步完成后用 `assemble` 刷新，只做增量改进。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：研究问题拆成 ≥1 条可检索子问题，每条含六要素标注；每步输入输出清楚。
- [ ] **实际效果/正确性**：每条标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+逐字 quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题均有 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖**全部**记录，排除项有理由；`evidence_map.verification` 统计核验。
- [ ] **使用增益**：多轮规划与术语扩展提高召回；全量筛选与透明打分减少返工；`python3 scripts/main.py selfcheck` 全 PASS。
- [ ] **创新增益**：证据溯源强化——每条结论绑定 locator+逐字 quote，本地 quote 规范化命中校验，孤儿结论/未命中引文显式单列；打分 `0.45·relevance + 0.30·evidence_strength + 0.15·metadata_complete + 0.10·source_reliability` 由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行幂等；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；`scripts/selftest.py` 三场景（正常/坏 JSON/空输入）本地可跑；他人可按 `references/` 复现完整检索。

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步建议，**不补造文献**。
- 缺软件/接口异常/限流/权限不足：记录 `errors`/`degradations`，改用本地材料（materials/PDF）补充并标注局限；无法继续时如实降级为 partial。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion.ambiguities` 注明并加限定词，或拆成两条查询分别记录；无法判断写 `pending_human`。
- 证据缺失：无 locator/quote 的结论不写进 relevance_reason，写入 `evidence_map.orphan_claims`；本地 quote 未命中写入 `unverified_quotes` 并提示复核。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出具体缺口。

## 7. 脚本（纯标准库，相对路径调用）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py set-stop --file /app/work/stop_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

复杂 JSON 用 `--file` 传文件，避免 shell 引号转义；`--inline` 仅用于简单单行。方法细则见 `references/method-and-checklist.md`。
