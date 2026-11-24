---
name: research-search-robust
description: >-
  科研问题驱动检索 Skill（鲁棒性变体）：把复杂科研问题拆解为可检索子问题，
  规划查询顺序与依赖，扩展同义/缩写/跨语言/上下位术语，多轮检索并逐条全量筛选，
  按透明规则打分排序，记录停止判断与遗漏，交付可复核的 result.json。
  空输入、坏 JSON、坏编码、缺参数、超大文件、输出目录不可写等异常均优雅降级且不编造。
  当需要围绕一个科研问题检索、筛选、排序文献并保留筛选依据（而非单篇精读、翻译或写作）时使用。
version: 1.1.0
---

# 科研问题驱动检索 Skill（鲁棒性变体）

## 1. 角色与何时使用

把"科研问题 → 子问题拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付"固化为可重复流程。适用：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇精读、全文翻译、写作润色、开放式问答。

**核心原则：不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数；无材料就写"材料不足"，绝不示例填充。** 脚本只做确定性持久化与组装，推理判断用模型请求完成。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 原文，需自行用 pdftotext 读取，不含预提取答案层）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig`→`utf-8`→`gb18030`→`latin-1` 顺序容错解码（BOM、坏编码、坏 JSON 均降级不崩溃）。
- 单文件超过 2MB（`MAX_TEXT_FILE`）跳过并记入 `degradations`；文件数上限 100。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`；输出目录不可写时回退到 work/ 并记录。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract`），其**全部字段原样回填**进 result.json：未填给 `null`/`[]`/`{}`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 强制）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）
- `research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*
- `results`*：included 候选，每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown
- `screening`*：**全量**筛选账本（included/excluded/uncertain/merged_duplicates），排除项不静默丢弃
- `evidence`*：查询/轮次/冲突/缺口日志（query/source/database/timestamp/rationale/kind）
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*（time/action/reason/status）
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）

脚本同时生成可编辑 `/app/output/screening.md`（全量筛选清单）、`/app/output/plan.md`（拆解/查询计划/术语扩展/停止判断）与 `/app/output/selfcheck.md`（自检报告）。本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求再按需产出。字段语义见 `references/result-schema.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

先落盘骨架再增量改进；**任何时刻中途失败，`/app/output/result.json` 都必须合法存在**。前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架，此后只用增量子命令更新同一份 result.json，禁止重新 `run` 覆盖计划。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 -B scripts/main.py run --input /input --output /app/output --work /app/work` | /input/ | result.json(partial)+case_summary | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间范围/比较/证据要求，形成 SQ1..SQn | case_summary | set-plan(sub_questions) | 2–4 分钟 |
| 2 规划 | 为子问题安排查询顺序与依赖（前置/并行） | sub_questions | set-plan(query_plan) | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位变体，每概念 ≤6 变体控发散 | key_concepts | set-plan(term_expansion) | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选记录 + evidence | ≤25 分钟 |
| 5 筛选 | `python3 -B scripts/main.py import --file`：逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 | records.json | 随检索进行 |
| 6 收敛 | 检查每子问题证据、冲突验证、边际收益，填 stop_decision | evidence | set-plan(stop_decision) | 3–5 分钟 |
| 7 组装 | `python3 -B scripts/main.py assemble` 写 result.json+screening.md+plan.md | 全部 | /app/output/ | ≤1 分钟 |
| 8 自检 | `python3 -B scripts/main.py selfcheck` 逐条 PASS/FAIL | result.json | 自检清单 | ≤1 分钟 |

建议时间分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每个步骤完成后用 `assemble` 刷新 result.json，只做增量改进。复杂 JSON 用 `--file` 传文件避免引号转义。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：拆成 ≥1 条可检索子问题，每条含对象/机制/方法/时间/比较/证据要求；每步输入输出清楚
- [ ] **实际效果/正确性**：每条结果元数据真实且逐字保留来源写法；结果与子问题真正相关；依据为 locator+quote 非空泛评语
- [ ] **实际效果/完整性**：每个子问题 ≥1 条 included 证据；未覆盖的列入 `uncertainty.coverage_gaps`
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖全部记录，排除项有理由
- [ ] **使用增益**：多轮规划与术语扩展提高召回；全量筛选+透明打分减少人工返工；`selfcheck` 全 PASS
- [ ] **创新增益**：六要素拆解、依赖化规划、受控扩展、停止阈值、透明打分（0.6·relevance+0.2·元数据+0.2·来源）脚本固化、可复现
- [ ] **可靠体验**：`--help` 退出码 0；空输入/坏 JSON/坏编码/缺参数/超大文件不崩溃且退出码 0；重复运行一致（幂等）；异常写 errors/degradations
- [ ] **生态贡献**：SKILL.md+scripts+references 齐全；他人可按 `references/` 独立复现一次完整检索

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写缺失与下一步建议，**不补造文献**
- 缺软件/接口异常/限流/权限不足：记 `errors`/`degradations`，改用本地 materials/PDF 补充并标注局限；无法继续时如实降级 partial 并说明
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍
- 术语严重歧义：`term_expansion` 注明歧义加限定词，或拆两条查询分别记录；无法判断写 `pending_human`，不猜测
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列具体缺口
- 空输入目录：`status=insufficient_evidence`；坏 JSON：`status=input_error`；超大文件：跳过并记 degradations——均退出码 0

## 7. 脚本与调用（纯标准库，相对路径）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
```

自检脚本：`python3 -B scripts/selftest.py`（覆盖空输入/坏 JSON/坏编码/超大文件/完整流程/幂等，期望 20/20 通过）。`--file`/`--inline` 均可，复杂 JSON 优先 `--file`。字段规范见 `references/result-schema.md`，方法细则与检查清单见 `references/method-and-checklist.md`。以上命令与字段均为示意，非正式案例结果。
