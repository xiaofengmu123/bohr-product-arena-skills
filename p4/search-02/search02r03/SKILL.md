---
name: deterministic-research-search
description: >-
  科研问题驱动检索 Skill（确定性版）：把复杂科研问题拆成带六要素的可检索子问题，规划依赖化查询顺序、
  做受控术语扩展、多轮检索并全量筛选每条记录，用脚本确定性组装 result.json 并保留来源、筛选依据、
  停止判断与遗漏说明，附带 selftest.py 自检与可复现的内容哈希。当需要围绕一个科研问题检索、筛选、
  排序文献并留可复核证据（而非单篇精读、翻译或写作）时使用。强调同一输入两次运行产物一致。
version: 1.1.0
---

# 科研问题驱动检索 Skill（确定性版）

## 1. 角色与何时使用

本 Skill 把「科研问题 → 子问题拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付」固化为确定性流程，供 Agent 在固定科研案例上运行。适合：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据与遗漏说明。不适合：单篇精读、翻译、写作润色、开放式问答。

核心原则：**不编造文献、标题、作者、年份、期刊、DOI、数值或评测分数**；无材料就写「材料不足」，绝不用示例填充。本包的设计取舍集中在**确定性 + 自检**：同一输入两次运行，result.json 的分析正文（除 `run_meta` 时间戳外）字节一致，并由 `manifest.json` 的 `deterministic_content_sha256` 保证可复核。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`；可能另有 `materials/`（PDF 等科研材料，即固定快照的论文原文）。
- **不假设固定键名**：先枚举 `/input/` 全部文件再解析；文本按 `utf-8-sig→utf-8→gbk→cp1252→latin-1` 顺序容错解码，坏 JSON/坏编码降级为 `errors`/`degradations`，不崩溃；单文件超过 2MB 跳过并记录。
- 工作目录 `/app/work/`（状态唯一真源 `state.json`），最终产物写 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output/schema/required_fields/contract`），其**全部字段必须原样回填**进 result.json；未获得的回填 `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，至少含（`*` 为强制）：

- `case_id`*、`status`*：`ok | partial | insufficient_evidence | input_error`；
- `research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*：纳入候选，每条含 rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown；
- `screening`*：**全量筛选账本**（included/excluded/uncertain/merged_duplicates），排除项不静默丢弃；
- `evidence`*：查询/轮次/冲突/缺口日志（query/source/database/rationale/kind）；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`change_log`*（step/action/reason/status）；
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`run_meta`；
- 题目 `output_contract` 的**全部字段**原样回填。

脚本同时生成：`/app/output/screening.md`（全量筛选清单）、`/app/output/plan.md`（拆解/查询计划/术语扩展/停止判断）、`/app/output/manifest.json`（内容哈希，供复现核验）。字段细则见 `references/result-schema.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

**硬性要求：前 3 分钟内先落盘一版 `status=partial` 骨架，之后只做增量更新，禁止重新 `init` 覆盖已录状态。** 任何时刻中断，`/app/output/result.json` 都必须合法存在。

先 `cd /skills/weekly-skill` 再以相对路径调用脚本：

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 -B scripts/main.py init --input /input --output /app/output --work /app/work` | /input/ | result.json(partial)+screening.md+plan.md | ≤3 分钟（硬性） |
| 1 拆解 | 识别对象/机制/方法/时间/比较/证据要求六要素，形成 SQ1..SQn | case_summary | set-plan --kind sub_questions | 2–4 分钟 |
| 2 规划 | 为子问题安排查询顺序与依赖（前置/并行） | sub_questions | set-plan --kind query_plan | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位/领域变体，每概念≤6 变体控发散 | key_concepts | set-plan --kind term_expansion | 2–3 分钟 |
| 4 检索 | paper_search/web_search/本地材料，按计划多轮执行 | plan | 候选 + evidence | ≤25 分钟 |
| 5 筛选 | `python3 -B scripts/main.py import --file ...`：逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 | records.json | 随检索进行 |
| 6 收敛 | 检查每子问题证据、冲突验证、边际收益，填 stop_decision | evidence | set-plan --kind stop_decision | 3–5 分钟 |
| 7 组装 | `python3 -B scripts/main.py assemble ...` 写 result.json + 三份 md/manifest | 全部 | /app/output/ | ≤1 分钟 |
| 8 自检 | `python3 -B scripts/main.py selfcheck --output /app/output --work /app/work` | result.json | PASS/FAIL 清单 | ≤1 分钟 |

原则：脚本是确定性辅助，不消耗模型请求；64 次模型请求用于拆解、相关性判断与查询调整。建议时间分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。复杂 JSON 用 `--file` 传文件，`--inline` 仅用于简单单行；`set-plan --kind` 支持 `status/research_question/sub_questions/query_plan/term_expansion/stop_decision/output_contract_values/status_notes/limitations/pending_human`。

## 5. 自检清单（逐条可勾选，对应评分维度）

- [ ] **实际效果/完成度**：研究问题拆成 ≥1 条六要素子问题，每条标注对象/机制/方法/时间/比较/证据要求；每步输入输出清楚。
- [ ] **实际效果/正确性**：结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题真正相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖**全部**记录，排除项有理由。
- [ ] **使用增益**：多轮规划与术语扩展提高召回；全量筛选与脚本打分减少返工；`selfcheck` 全 PASS。
- [ ] **创新增益**：六要素拆解、依赖化查询规划、受控术语扩展、停止阈值；打分公式 `score = 0.55·relevance + 0.20·evidence + 0.15·metadata + 0.10·reliability` 由脚本计算、可复现。
- [ ] **可靠体验**：`--help` 退出码 0；空输入/坏 JSON/坏编码/超限文件不崩溃且退出码 0；`selftest.py` 31 项全 PASS；重复运行分析正文字节一致。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人按 `references/` 说明可独立复现一次完整检索。

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步建议，**不补造文献**。
- 缺软件/接口异常/限流/权限不足：记录到 `errors`/`degradations`，改用本地材料（materials/PDF）并标注局限；无法继续时降级 `partial` 并说明。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；同指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：在 `term_expansion` 注明并加限定词，或拆两条查询分别记录；无法判断的字段写 `pending_human`，不猜测。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列具体缺口。

## 7. 确定性保证（本包核心取舍）

- 所有顺序固定：JSON 用 `sort_keys` 序列化，记录按 rank→title→year 稳定排序，证据按步号排序。
- 墙钟时间只出现在 `run_meta.started_at/finished_at/duration_seconds`，**不进入分析正文**；`change_log` 用确定性 `step` 序号，不含时间戳。
- `manifest.json` 写 `input_sha256`/`state_sha256`/`deterministic_content_sha256`；后者 = result.json 去掉 `run_meta` 后的排序序列化哈希，任何人可据此核验两次运行一致。
- 安装后运行 `python3 -B scripts/selftest.py` 可一键验证健壮性与确定性（空输入、坏 JSON、坏编码、超限文件、完整流程、重复运行）。

## 8. 脚本（纯标准库，相对路径调用）

先 `cd /skills/weekly-skill`；`main.py` 为薄入口，实现见 `core_impl.py`，自检见 `selftest.py`。

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py init --input /input --output /app/output --work /app/work
python3 -B scripts/main.py set-plan --kind sub_questions --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py record-evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

以上命令与字段为接口示意；正式案例按 `/input/` 实际内容填写，不预写答案、不伪造评测分数。